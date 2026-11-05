# Mneme

Mneme is a single-user flashcard app for a home Kubernetes cluster. Notes and review progress live on the always-on Ubuntu server. Only flashcard generation requests inference from the GPU worker. The app checks for the configured model, sends Wake-on-LAN if the worker is unreachable, and waits up to about 90 seconds for Ollama. It requests immediate model unload after generation.

| Machine | Pilot role |
| --- | --- |
| Always-on Ubuntu x86_64 server | K3s control plane and always-on Mneme app |
| Gaming PC, Windows 11 and RX 7900 XT | Private Ollama endpoint on the wired LAN. Join K3s as a GPU worker after booting Linux. |
| Spare RX 9060 XT | Optional second Linux inference host after installing the card and checking its VRAM. |
| Mac with Obsidian | Imports selected Markdown files and extracts text from Goodnotes PDFs. |

GPU workers serve separate requests. Ordinary Kubernetes scheduling and this Ollama setup place a model on one machine. Start with the 7900 XT for 27B. [K3s requirements](https://docs.k3s.io/installation/requirements) · [Ollama GPU support](https://docs.ollama.com/gpu)

## What you can do

- **Import an Anki deck.** Upload a modern or legacy `.apkg`, or a tab-separated text export with front and back in the first two columns. Anki packages can contain up to 75,000 cards and 100,000 ZIP entries. The configured upload limit also caps total extracted media. Tab-separated text exports support up to 5,000 cards and 64 MiB. The importer supports common Basic, reversed, and simple cloze cards. It reports skipped cards and unavailable media. Custom scripts, styling, image occlusion, and Anki review history stay outside this import. [Anki deck export](https://docs.ankiweb.net/exporting.html)
- **Study on any authorized device.** Choose a deck and flip a card to see **Again**, **Hard**, **Good**, and **Easy** with their next intervals. Choose **Browse** to see every card without changing its schedule. Graded reviews follow the study account across devices.
- **Spaced repetition.** Mneme uses fixed SM-2-style settings with 1- and 10-minute learning steps. Good graduates the card at one day, while Easy can graduate it at four days. Missing a graduated card starts a 10-minute relearning step. Graduated cards start at 250% ease and have a one-year interval cap. Existing due dates are preserved on upgrade. [Anki scheduling rules](https://faqs.ankiweb.net/what-spaced-repetition-algorithm.html)
- **Keep reviews responsive.** The browser loads up to 12 cards at a time and preloads the next card’s first question image. Use Space to reveal the answer, 1–4 to rate it, or Right Arrow to advance in browse mode. Reviews require a connection.
- **Turn notes into cards.** Paste or upload text and Markdown, or use the Mac bridge for Obsidian and Goodnotes. Choose a note section, generate up to five drafts with Ollama, and edit or approve each card with its source excerpt visible.
- **Flashcard chat.** Give directions, paste an article excerpt, or choose a PDF, text, or Markdown file within the configured upload limit. Select up to six PDF pages. Ollama uses up to 3,500 characters per request. Searchable PDFs work directly, while scans need OCR text. PDF uploads are deleted after extraction, and text files send only their opening excerpt. A follow-up uses the visible drafts. Select and edit the cards you want to save. The chat directions, excerpts, and drafts are not kept in the study library.
- **Export to Anki.** Download active cards as a two-column TSV. The TSV exports card text. Source media and scheduling history are stored separately.

Reuploading an Anki package under the same deck name updates matching card content and keeps Mneme review progress. Imported cards begin with a fresh schedule. Decks, media, and progress live on the Ubuntu server, so the gaming PC can sleep during import, browsing, review, and export.

The pilot uses a single study account. For a wider release, separate users and decks before opening access. The web app can move to a hosted container with managed PostgreSQL and object storage for media. Inference can remain on a private GPU worker or move to a hosted provider with its own queue and credentials.

## Run the Ubuntu server first

The app can run while the GPU worker is offline. Note import, review, and Anki export only require the Ubuntu server. To install from this workspace on your Mac, open a **local** Terminal tab and run:

```bash
bash scripts/deploy_homeserver.sh YOUR_USER@YOUR_SERVER
```

The script uploads the app over SSH and runs the Ubuntu bootstrap through `sudo`. It creates a single-server K3s cluster if K3s is absent, using the private IPv4 interface with the default route. On a new install, it disables bundled Traefik and ServiceLB, which avoids taking over ports 80 and 443. It reuses a running K3s server and the app's existing password and data on subsequent runs. The bootstrap checks that K3s, persistent storage, and the app are ready. Firewall rules for the eventual Linux worker depend on its LAN address. [K3s installation](https://docs.k3s.io/quick-start) · [K3s network ports](https://docs.k3s.io/installation/requirements#networking)

If you already copied the project to the server, run `sudo bash scripts/bootstrap_homeserver.sh` from the copied directory. Retrieve the app password directly in your Ubuntu SSH session and save it in your password manager:

```bash
sudo k3s kubectl -n home-study get secret home-study-auth -o jsonpath='{.data.password}' | base64 -d
echo
```

Open the app tunnel from a local Terminal tab on your Mac:

```bash
ssh -N -L 8787:127.0.0.1:8787 YOUR_USER@YOUR_SERVER
```

Visit `http://127.0.0.1:8787` and sign in as `study`. To use the app away from home, connect the phone or iPad to Tailscale and open the same private HTTPS address in Safari. Check existing [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve) configuration and choose a free private HTTPS port on Ubuntu:

```bash
sudo tailscale serve status
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8787
```

For an outside user, share the server only after verifying that the share grants access to the app port. Keep SSH and Kubernetes admin access restricted to administrators. [Tailscale machine sharing and access rules](https://tailscale.com/docs/features/sharing#sharing-and-access-control-policies)

The single app replica uses host networking for wake packets and listens on Ubuntu loopback. The SQLite database and imported media files live in the local-path PVC on Ubuntu. The PVC requests 2 GiB, while this provisioner uses available disk space without enforcing that number as a quota. Keep backups of both SQLite and the adjacent `study-media` directory. Large uploads stream in 8 MiB chunks and can resume from the last completed chunk in the same tab. Temporary uploads live on disk outside the persistent volume and idle uploads expire after one hour. Deck import preserves extracted cards and media and removes the original archive. [K3s persistent storage](https://docs.k3s.io/add-ons/storage)

The homelab deployment temporarily sets `STUDY_UPLOAD_MAX_BYTES` to 100 GB (100,000,000,000 bytes) per Anki package or chat source. Remove the override from `k8s/app.yaml` and redeploy to restore the 10 GB default. A deck import needs free space for both the staged archive and the extracted media. [Kubernetes temporary storage](https://kubernetes.io/docs/concepts/storage/volumes/#emptydir)

## 1. Benchmark Qwen on the gaming PC

Install or update [Ollama for Windows](https://docs.ollama.com/windows) and use a [compatible AMD driver](https://docs.ollama.com/gpu). In PowerShell:

```powershell
ollama pull qwen3.8:27b
$body = @{
    model = 'qwen3.8:27b'
    messages = @(@{role = 'user'; content = 'Explain what a Kubernetes Service does in three sentences.'})
    options = @{num_ctx = 4096}
    stream = $false
    think = $false
    keep_alive = '5m'
} | ConvertTo-Json -Depth 4
Invoke-RestMethod http://127.0.0.1:11434/api/chat -Method Post -ContentType application/json -Body $body
ollama ps
ollama stop qwen3.8:27b
```

The Q4_K_M download is about 18 GB. The RX 7900 XT has 20 GB of VRAM. Runtime buffers, context, and other applications also consume VRAM. Test with the game closed and check for `100% GPU` in `ollama ps`. If some layers offload to CPU or the model fails to load, try Qwen3.6-27B and measure it the same way. The current 3.6 GGUF is also listed around 18 GB, so a smaller quantization or model may be necessary if both have trouble. [Qwen3.8](https://ollama.com/library/qwen3.8:27b) · [Qwen3.6](https://ollama.com/library/qwen3.6:27b) · [AMD GPU specifications](https://rocmdocs.amd.com/en/docs-7.2.4/reference/gpu-arch-specs.html) · [Checking GPU offload](https://docs.ollama.com/context-length)

## 2. Test Wake-on-LAN on the wired network

Give the gaming PC a stable LAN IP, ideally a DHCP reservation. In its firmware and Windows Ethernet adapter settings, enable wake by magic packet. Start with Windows Sleep. Use `powercfg /a` to see which sleep states are available and `Get-NetAdapter` to find the wired adapter MAC. Obtain the wired subnet's broadcast address from `ip -4 addr` on Ubuntu. Use the LAN broadcast address, since the Tailscale SSH address is for administration. [Microsoft wake behavior](https://learn.microsoft.com/en-us/troubleshoot/windows-client/deployment/wake-on-lan-feature)

Copy the workspace to Ubuntu. Put the PC to sleep manually, then from Ubuntu run this with the actual wired MAC and LAN broadcast address:

```bash
python3 wake.py 'PC_ETHERNET_MAC' 'LAN_BROADCAST'
```

Test physical wake before connecting it to the app. Repeat from the intended sleep state. A dual-boot machine wakes or boots according to its firmware and boot configuration, so a cold boot should select Linux automatically if Linux is meant to be the worker.

For Windows service connectivity, set `OLLAMA_HOST` to the PC's LAN IP and port 11434, set `OLLAMA_NO_CLOUD=1`, then restart Ollama so it reads those settings. Allow TCP 11434 in Windows Firewall **only** from the Ubuntu LAN IP. Ollama's local API trusts callers on that port. Confirm `curl http://PC_LAN_IP:11434/api/tags` from Ubuntu with the PC awake. Check after a resume, and after a reboot if the pilot needs to wake from a full shutdown. [Ollama Windows configuration](https://docs.ollama.com/windows) · [Ollama local-only mode](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)

Use the PC's own idle-sleep settings for the Windows pilot. Test that the machine stays awake through generation, then goes back to sleep while idle. Use at least 15 minutes for the first test and check actual behavior with `powercfg /requests`. The app controls wake and releases the model. Automatic OS shutdown and inference-aware sleep inhibition are separate follow-up work for the Linux worker.

## 3. Connect the verified worker

Configure the verified worker and restart the app. Replace the placeholders using the values from step 2:

```bash
sudo k3s kubectl -n home-study create configmap home-study-worker \
  --from-literal=OLLAMA_URL='http://PC_LAN_IP:11434' \
  --from-literal=OLLAMA_MODEL='qwen3.8:27b' \
  --from-literal=WOL_MAC='PC_ETHERNET_MAC' \
  --from-literal=WOL_BROADCAST='LAN_BROADCAST' \
  --dry-run=client -o yaml | sudo k3s kubectl apply -f -
sudo k3s kubectl -n home-study rollout restart deploy/home-study
sudo k3s kubectl -n home-study rollout status deploy/home-study
```

Use `qwen3.6:27b` in the ConfigMap if that model performs better. Leave both WOL values out to test inference while the worker is already awake. After enabling wake, verify that importing and reviewing leave the PC asleep, a generation wakes it, and the PC returns to sleep after the OS idle period. Save periodic SQLite backups from the Ubuntu volume using SQLite's online backup command.

## 4. Sync notes from the Mac

Select a dedicated study folder in the Obsidian vault and a local folder for Goodnotes PDFs. For Goodnotes, export PDFs into it or configure iPad/iPhone Auto Backup with PDF output and point the bridge to the synced local copies. The Mac must be running the bridge. Goodnotes Auto Backup is one-way and its upload schedule determines when a new version becomes available. [Obsidian storage](https://obsidian.md/help/Files%2Band%2Bfolders/How%2BObsidian%2Bstores%2Bdata) · [Goodnotes Auto Backup](https://support.goodnotes.com/hc/en-us/articles/7352786555279-How-to-Set-Up-Auto-Backup-in-Goodnotes)

On the Mac, with Python 3 and Xcode Command Line Tools:

```bash
swiftc scripts/pdf_text.swift -o /tmp/homelab-pdf-text
read -rs 'STUDY_PASSWORD?App password: '   # zsh
export STUDY_PASSWORD
python3 scripts/sync_notes.py \
  --url http://127.0.0.1:8787 \
  --obsidian '/path/to/vault/Study' \
  --goodnotes '/path/to/Goodnotes/Study' \
  --pdf-reader /tmp/homelab-pdf-text
```

Either source flag can be used on its own. Use the private HTTPS URL in place of the SSH tunnel when available. Add `--once` for a one-time import. The bridge polls every 15 seconds and waits for a stable file before sending its text. PDF conversion uses macOS Vision and PDFKit. Inspect OCR before approving cards. Updates replace the mirrored note and invalidate pending drafts while preserving approved cards and review progress. Renamed and deleted files leave their last imported copy in the app.

Approve or edit each generated draft, then use **Due for review**. Again schedules a ten-minute retry. Good doubles the interval after the first one-day review. **Export to Anki** downloads approved Front and Back fields as TSV for Anki's Basic note type. [Anki TSV import](https://docs.ankiweb.net/manual/importing/text-files)

## 5. Join the gaming PC as a Linux GPU worker

Use a Linux release and kernel supported by the AMD ROCm version required by the Ollama image. Verify the AMD driver and `/dev/kfd` on the new Linux boot first. Join the wired cluster as a K3s agent using the private agent token and Ubuntu LAN address. Set `INSTALL_K3S_VERSION` to the version reported by `k3s --version` on the server. Label the worker `homelab-role=gpu-7900xt`, install the AMD Kubernetes device plugin, and verify the node advertises `amd.com/gpu` before deploying the ROCm manifest. [AMD Linux matrix](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/native_linux/native_linux_compatibility.html) · [K3s agent setup](https://docs.k3s.io/quick-start#install-script) · [AMD device plugin](https://instinct.docs.amd.com/projects/k8s-device-plugin/en/latest/user-guide/installation.html)

```bash
sudo k3s kubectl get node GPU_NODE -o jsonpath='{.status.allocatable.amd\.com/gpu}'
sudo k3s kubectl apply -f k8s/ollama-amd.yaml
sudo k3s kubectl -n home-study rollout status deploy/ollama-amd
sudo k3s kubectl -n home-study exec deploy/ollama-amd -- ollama pull qwen3.8:27b
```

Verify GPU offload again. Then update the app's `OLLAMA_URL` to `http://ollama-amd.home-study.svc.cluster.local:11434`. The model cache stays on the GPU worker's boot drive. Retest wake with Linux and check that the node and Ollama endpoint recover after resuming. For a second Linux GPU worker, benchmark it independently and add a separate service and request routing after the first worker is stable.

## Local development

With Python 3.14, install `python3 -m pip install --require-hashes -r requirements.txt` for PDF extraction. For generation, use a reachable Ollama endpoint. Set `STUDY_PASSWORD` and optionally `OLLAMA_URL` and `OLLAMA_MODEL`, then run `python3 app.py`. Open `http://127.0.0.1:8787`. The default database is `study.sqlite3` in the working directory. If inference is unavailable, note import and flashcard review remain available.

Run the offline tests with `python3 -m unittest discover -s tests -v`. They use temporary databases and a mock Ollama worker to cover Anki import, review scheduling, draft generation, and wake retries.

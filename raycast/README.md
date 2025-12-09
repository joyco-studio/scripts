# Raycast Scripts

Scripts meant to be used with [Raycast](https://raycast.com/) for quick workflow integration.

## Installation

1. Open Raycast → Settings → Extensions → Add
2. Click "Add Script Directory"

   ![add-script-directory](https://qfxa88yauvyse9vr.public.blob.vercel-storage.com/add-script-directory)
3. Pull the repo and select this `raycast` folder or copy the scripts to you local folder.
4. All scripts will be automatically available in Raycast

This approach lets Raycast watch the folder, so any new scripts added here will automatically appear in your commands.

---

## Scripts

### `get-ip.sh` 🌍

Copies your local private IP address to clipboard. Useful for sharing your dev server with other devices on the network.

**Usage:** Run from Raycast, IP is automatically copied.

---

### `killport.sh` 💀

Kill a process running on a specific port. No more hunting for PIDs.

**Usage:** Run from Raycast, enter the port number when prompted.

```
> Kill Port
> Enter port: 3000
→ "Killed process 12345 on port 3000"
```

---

### `pull-quicklinks.sh` 🔗

Downloads the latest quicklinks.json from the Joyco registry and saves it to your Downloads folder.

**Usage:** Run from Raycast, file is automatically downloaded.

```
> Pull Quicklinks
→ "✓ Downloaded 12 quicklinks to ~/Downloads/quicklinks-pull-12-09-2024.json"
```

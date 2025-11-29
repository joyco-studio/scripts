# Raycast Scripts

Scripts meant to be used with [Raycast](https://raycast.com/) for quick workflow integration.

## Installation

1. Open Raycast → Settings → Extensions → Add
2. Click "Add Script Directory"
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


# Raycast Scripts

Scripts meant to be copy-pasted directly into [Raycast](https://raycast.com/) for quick workflow integration.

## Installation

1. Open Raycast → Extensions → Script Commands
2. Create a new script command
3. Copy the contents of the desired script
4. Save and use

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


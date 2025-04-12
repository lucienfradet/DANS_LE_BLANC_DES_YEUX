# Code case for the "Dans le blanc des yeux project"

### Wifi blocking
To block or unlbock wifi to force Hat LTE, use:

```bash
sudo rfkill unblock wifi
sudo rfkill block wifi
```

### Screen brightness
```bash
/usr/share/applications/brightness64bit
```

### Systemctl
```bash
# Stop the service
sudo systemctl stop dans-le-blanc.service

# Start the service
sudo systemctl start dans-le-blanc.service

# Restart the service
sudo systemctl restart dans-le-blanc.service

# Disable autostart on boot
sudo systemctl disable dans-le-blanc.service

# Enable the service to start on boot
sudo systemctl enable dans-le-blanc.service

# Check status
sudo systemctl status dans-le-blanc.service

# Checking logs!
# View all logs for the service
journalctl -u dans-le-blanc.service

# Follow live logs (like tail -f)
journalctl -f -u dans-le-blanc.service

# View only recent logs
journalctl -u dans-le-blanc.service -n 100

# View logs since boot
journalctl -u dans-le-blanc.service -b
```

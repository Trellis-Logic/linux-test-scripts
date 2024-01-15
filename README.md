# Linux Device Tests
Scripts used to test Linux based devices

# Setup Python

Run 
```
pip3 install -r requirements.txt
```
## Rolling Reboot Tests

Run
```
python3 rolling_reboot_test.py --device <ip addr>  --user user --password password
```
Where `user` and `password` describe an SSH access user

### Running with non-root Accounts

When running with non root accounts, add a line like this to your /etc/sudoers file:
```
user ALL=NOPASSWD: /sbin/reboot
```
Where `user` is the username passed with the `--user` argument.

Then run the test case with the `--sudo` argument specified to use sudo with the reboot command

## Update Tests

### swupdate (tegra platforms)

Run
```
python3 swupdate_test_tegra.py --device <ip addr> --updatefile <file>
```

To run a swupdate test for OE4T tegra based platforms which include swupdate with A/B rootfs.

The test will perform 100 consecutive updates, verifying boot and rootfs slots are switched synchronously after each.

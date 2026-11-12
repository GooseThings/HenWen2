# Running HenWen next to the ASL Web UI

AllStarLink's `asl-web-ui` package (ASL3, Debian 13) is a browser admin UI
for the node. It is bundled with `asl3-appliance` 3.0.0, so an ordinary
`apt upgrade` can install it on a HenWen box. What HenWen hands over to it,
and why, is in [`docs/ASL-WEB-UI-PLAN.md`](../docs/ASL-WEB-UI-PLAN.md). This
directory covers making the two coexist.

## Apache: `apply.sh`

The package runs `a2enconf asl-web-ui`, which serves `/asl-web-ui/` on
**every** Apache virtual host. On a server that also hosts other sites, the
node's admin UI shows up on all of them. On HenWen's own vhost, HenWen's
catch-all `ProxyPass /` sends `/asl-web-ui/` to Flask, so the UI is
unreachable there.

```bash
sudo bash /opt/HenWen2/asl-web-ui/apply.sh     # safe to re-run
sudo bash /opt/HenWen2/asl-web-ui/rollback.sh  # undo the last run
```

For every HenWen vhost, `apply.sh` adds an `IncludeOptional` of the package's
conf plus `ProxyPass /asl-web-ui !`, both above HenWen's catch-all. It then
disables the global conf. An admin's `a2disconf` is remembered, so package
upgrades don't turn it back on. It is safe to run before the package is
installed, but the package's first install turns the global conf on, so run
it again after installing `asl-web-ui`. Every vhost it touches is backed up
to `/root/henwen-asl-web-ui-backup-<stamp>/`.

Afterwards the UI is at `https://<HenWen hostname>/asl-web-ui/` and on no
other site. It refuses logins over plain HTTP.

## AMI

HenWen logs into AMI as its own `[henwen]` user, not `[admin]`. The UI's
**Asterisk > Settings > AMI Secret** changes `[admin]` and updates only
Allmon3. Run `sudo bash /opt/HenWen2/ami-setup.sh` once on an older install
to create HenWen's user. Manager > AMI Diagnostics warns while HenWen is
still on `admin`.

## HTTPS certificates

Let HenWen's `tx-spike/setup-https.sh` own the certificate for HenWen's
hostname, and **don't request that same hostname from the UI's HTTPS
page**. The UI runs `certbot certonly --apache` and, by default, writes its
own `sites-available/asl-web-ui-https.conf` for the names it is given. Two
port-443 vhosts with the same `ServerName` means Apache serves whichever loads
first. The UI's certificate for any *other* name is fine.

## `/etc/asterisk/custom/`

HenWen writes its generated per-user SIP files (`henwen-sip-users*.conf`)
there. The UI's **Nodes > Customizations** lists only files carrying a
`;MENU:` line, which HenWen's never do, so they don't appear. The UI's
**Manual Config Edit** can open them, but HenWen regenerates them on every
account change, so edit the accounts in HenWen Manager instead.

## Node numbers

The UI can rename or remove a node. HenWen's connectors, ID monitors,
lockouts, favorites and SIP dialplan refer to nodes by number and do not
follow a rename: re-point them in HenWen Manager after renaming a node.

# Pointing the desktop app at a server you already run

By default the OpenConstructionERP desktop application runs its own server and its own
database on the computer it is installed on. Nothing about that changes, and on a machine
where nobody has followed this document, nothing in it applies: the first run asks no
question and offers no chooser.

This document is for the other case. If your organisation already runs one
OpenConstructionERP server, you can point desktop installations at it, so that everybody
works in the same projects instead of in a separate database per desk.

## The four places the answer can come from

The launcher asks four questions in order and stops at the first one that answers. The
order is fixed and is:

1. **The setting inside the application.** Settings, Advanced, Application server. Written
   only when somebody explicitly chooses; a user who has never opened that card does not
   have this layer at all.
2. **The `OCE_SERVER_URL` environment variable.**
3. **The configuration file deployed on the machine** (paths below).
4. **The default**, which is to run a server on this computer.

### Why that order

The setting a user makes is on top because it is the only one of the four that can be
changed without a text editor. A person whose configured server is wrong has to be able to
get back to a working application without finding a file, and that is only true if their
own choice outranks everything below it.

It is worth being explicit that **this is not a policy mechanism.** The order above cannot
pin a machine to a server against the wishes of somebody sitting at it, and it is not
designed to. If you need that, it has to come from the server, not from the client: the
desktop application is open source and runs as the user, so anybody who can run it can
point it anywhere. Deploy the file to a location your users cannot write, by all means,
and understand that it sets a default rather than a restriction.

The environment variable sits above the deployed file because a variable is per launch and
a file is per machine. It is how you test one desk against a staging server for an
afternoon without editing the file that the whole fleet shares, and without having to
remember to put it back.

## The environment variable

```
OCE_SERVER_URL=https://erp.example.com
```

Set it to `local` to force a start on this computer, overriding a deployed file that says
otherwise:

```
OCE_SERVER_URL=local
```

An unset or empty variable means "this layer says nothing", and the launcher moves on to
the file. That is deliberate: an exported-but-empty variable is the normal residue of a
shell profile and should not stop a machine from starting.

## The configuration file

One file per machine, in a conventional location that an unprivileged user cannot write:

| Platform | Path |
|---|---|
| Windows | `%ProgramData%\OpenConstructionERP\config.json` |
| macOS | `/Library/Application Support/OpenConstructionERP/config.json` |
| Linux | `/etc/openconstructionerp/config.json` |

```json
{
  "backend_mode": "remote",
  "server_url": "https://erp.example.com"
}
```

To send a machine back to running its own server:

```json
{
  "backend_mode": "local",
  "server_url": ""
}
```

Keys this version does not recognise are ignored rather than rejected, so a file that
carries settings for a newer release is still read by an older one.

A file that cannot be read, or that is not JSON, counts as saying nothing. It does not
count as saying `local`: the launcher falls through to the layer below it, which with
nothing below it is the ordinary local start.

## What makes a valid server address

The launcher checks the address before it uses it, and refuses one it cannot use rather
than opening a blank window onto it.

- `http` or `https` only.
- A host is required. A bare `erp.example.com` with no scheme is refused rather than
  guessed at, because guessing `https` would be silent and would strand anybody who meant
  `http` on their own network.
- A port is fine: `http://192.168.1.20:8732`.
- A path is fine, for a reverse proxy that publishes the application under a prefix:
  `https://intranet.example.com/erp`.
- No user name or password. `https://admin:secret@erp.example.com` is refused rather than
  quietly stripped, because a password in a file deployed across a fleet is a leak, and
  silently dropping it would hide the leak while leaving it in the file.
- No query string and no `#` fragment. Give the address of the server, not of a page on
  it.
- No invalid percent escapes. `%zz` is refused. Note that this is not the same as "it
  parses": URL parsers accept `%zz` and carry it through untouched, so an address that
  parses is not automatically an address that is safe to use.

The address is canonicalised before it is stored, so a trailing slash is added and a host
is lower-cased. The settings card shows the canonical form after saving, which is what the
application will actually use.

Plain `http` to a host that is not on this machine is allowed, because a site office
server on a local network is a normal deployment in this industry. It is worth saying
plainly that everything on such a connection, including credentials, crosses the network
in the clear. Use `https` wherever you can terminate it.

## When the server cannot be reached

The application does not open a blank window. The startup screen names the server that was
tried, names which of the four layers configured it, says what went wrong (refused,
timed out, answered but is not an OpenConstructionERP server, answered with an HTTP
error), and offers a button that starts a server on this computer instead. That button
records the choice before restarting, so the restart does not come straight back to the
same screen.

If the configured server is reachable but is the wrong one, there is no failure screen to
carry that button. In that case use the tray icon menu, which carries a **Use a server on
this computer** item whenever a remote server is configured. The tray belongs to the
launcher rather than to the web page, which is why it works in this state.

## What the desktop app can do in remote mode, and what it cannot

One difference is worth knowing about before you deploy this.

In remote mode, the application is served by a server whose address a person typed in, and
that address is granted no native desktop commands at all. This is deliberate. Granting
the desktop command surface to an arbitrary origin would be a materially different product
from the one we ship, so we do not.

The visible consequence is that **outbound links do not open a browser window in remote
mode.** Clicking a link to the documentation or to an external site puts the address on
screen with a copy button instead of opening it. Nothing breaks and nothing fails
silently, but it is a click-then-paste rather than a click.

The settings card inside the application cannot change the setting in remote mode, for the
same reason: the page asks the launcher a question and the launcher declines to answer a
page it did not serve. Where the card appears at all it is read-only, naming the server the
window is connected to and pointing back at the tray menu. Treat the tray menu as the route
that is always there, and the card as a convenience that is not.

## Changing the setting from inside the application

Settings, Advanced, **Application server**. The card appears only in the desktop build.

A change takes effect the next time the application is opened, never in the running
window. Repointing a live session at a different database would leave every open form,
every cached query and every unsaved edit belonging to the server they came from.

The card also carries **Use the default for this computer**, which clears the user's own
choice and hands the decision back to the environment variable and the deployed file. On a
managed machine that is the way back to being managed, and without it a user who once
chose could never return.

## Where the user's own choice is stored

`~/.openestimate/server-choice.json`, beside the launcher's other per-user state. It does
not exist until somebody makes a choice, and its absence is what lets the layers below it
be reached. Deleting it is equivalent to pressing **Use the default for this computer**.

Every write of that file from inside the application is recorded in
`~/.openestimate/desktop-launcher.log`, naming the address that was stored. The setting
decides where the next start sends this user's password, so if you are ever asked to
explain why a machine is talking to a server nobody chose, that log is where the answer
is. It is a record and not a permission check: it tells you afterwards, it does not ask
first.

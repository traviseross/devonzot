#!/usr/bin/env bash
#
# install-devonthink-tls-identity.sh
#
# Installs a real (CA-issued) TLS identity into the login Keychain so the
# DEVONthink MCP server can present it when "Accept connections" is set to
# "From the local network". Run on the Mac that HOSTS DEVONthink for the
# network. For DEVONzot v1 that is the iMac; the MBP is only needed if/when
# it becomes a second MCP endpoint.
#
# NOTE: iCloud Keychain does NOT sync certificate identities / private keys,
# so this is a per-machine, one-time step — running it on one Mac does not
# propagate to the other.
#
# Usage:
#   ./install-devonthink-tls-identity.sh <hostname> [cert.pem] [key.pem]
# Example:
#   ./install-devonthink-tls-identity.sh imacdevonthink.traviseross.com
#
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage:   $0 <hostname> [cert.pem] [key.pem]" >&2
  echo "Example: $0 imacdevonthink.traviseross.com" >&2
  exit 1
fi
HOST="$1"
CERT="${2:-./cert.pem}"
KEY="${3:-./key.pem}"
DT_APP="/Applications/DEVONthink.app"
# The process that actually SERVES TLS is the MCP helper, not DEVONthink.app —
# it must be authorized to read the private key or macOS prompts for a keychain
# "always allow" the first time the server binds. Grant both.
DT_MCP="/Applications/DEVONthink.app/Contents/Library/LoginItems/DEVONthink MCP.app"
LOGIN_KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
P12="${TMPDIR:-/tmp}/dt-identity-$$.p12"
trap 'rm -f "$P12"' EXIT   # never leave the key bundle lying around

# ── Prereq: the PEM cert+key for $HOST must already be here. They come from
#    your Caddy (Cloudflare DNS challenge). Fetch them first, e.g.:
#      scp server:<caddy-cert-dir>/${HOST}/${HOST}.crt ./cert.pem
#      scp server:<caddy-cert-dir>/${HOST}/${HOST}.key ./key.pem
#    (Caddy's .crt is the full chain — exactly what we want.) ──
[ -f "$CERT" ] || { echo "ERROR: missing cert '$CERT' — scp it from Caddy first."; exit 1; }
[ -f "$KEY"  ] || { echo "ERROR: missing key  '$KEY' — scp it from Caddy first.";  exit 1; }

# ── Fail fast if the cert doesn't actually cover $HOST. A hostname mismatch
#    means the client rejects the TLS handshake, so catch it now, not later.
#    NOTE: macOS ships LibreSSL, whose `x509` has no `-ext` flag — we read the
#    SANs out of `-text` instead (verified against LibreSSL 3.3.6). ──
echo "── Cert being installed ──"
/usr/bin/openssl x509 -in "$CERT" -noout -subject || true
/usr/bin/openssl x509 -in "$CERT" -noout -text 2>/dev/null | grep -A1 "Subject Alternative Name" || true
if ! /usr/bin/openssl x509 -in "$CERT" -noout -text 2>/dev/null | grep -q "DNS:$HOST"; then
  echo "WARNING: '$HOST' not found in the cert's SANs (looked for DNS:$HOST)."
  echo "         Unless this is a wildcard cert covering \$HOST, the client will"
  echo "         reject the handshake on a name mismatch."
  if [ -t 0 ]; then
    read -r -p "Continue anyway? [y/N] " ok; [ "$ok" = "y" ] || exit 1
  else
    echo "         (non-interactive run — continuing; double-check the hostname.)"
  fi
fi

# ── Throwaway passphrase: auto-generated, used only to protect the temp .p12
#    during the export→import handoff, then discarded with the file (trap
#    above). You never type it and nothing stores it. This is what makes the
#    script prompt-free and safe to run over SSH. ──
P12PASS="$(/usr/bin/openssl rand -hex 16)"

# ── Step A: bundle cert+key into a PKCS#12 identity (macOS imports .p12, not
#    loose PEM). We call Apple's LibreSSL at /usr/bin/openssl on purpose — a
#    Homebrew openssl@3 produces a .p12 the Keychain frequently can't read. ──
/usr/bin/openssl pkcs12 -export -inkey "$KEY" -in "$CERT" \
  -out "$P12" -passout "pass:$P12PASS"

# ── Step B: import into the login Keychain, pre-authorizing DEVONthink to use
#    the private key. If run over SSH and this fails with the keychain locked,
#    run first (it prompts for your login password):
#        security unlock-keychain "$LOGIN_KEYCHAIN"
security import "$P12" -k "$LOGIN_KEYCHAIN" -P "$P12PASS" -T "$DT_APP" -T "$DT_MCP"

# ── Step C: confirm the identity is present and valid (you should see $HOST).
#    A real CA-issued cert appears under "valid identities"; a self-signed one
#    won't (it's untrusted) — so we fall back to listing all identities. ──
echo "── Valid identities now in the login keychain ──"
security find-identity -v "$LOGIN_KEYCHAIN" || true
if ! security find-identity -v "$LOGIN_KEYCHAIN" | grep -q "$HOST"; then
  echo "NOTE: '$HOST' not listed as a *valid* identity. For a real CA-issued cert"
  echo "      it should be; if it isn't, check trust/expiry. All imported identities"
  echo "      matching '$HOST':"
  security find-identity "$LOGIN_KEYCHAIN" | grep -i "$HOST" || echo "      (none — import may have failed)"
fi

cat <<EOF

✅ Identity installed. Next, in the DEVONthink GUI on THIS Mac:
   Settings → AI → MCP:
     • TLS Certificate    → select the identity for $HOST
     • Accept connections → From the local network
   (Leave the bearer token as-is — it's already matched across your Macs.)

   Do NOT switch to "From the local network" before selecting the cert, or the
   MCP server will refuse to start.
EOF

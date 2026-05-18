#!/usr/bin/env bash
# scripts/provision-oracle.sh — Provision an Oracle Cloud ARM instance for AlphaDivision.
#
# Creates a VM.Standard.A1.Flex instance (4 OCPU / 24 GB RAM — always free tier).
# Retries across availability domains until capacity is found.
#
# Prerequisites:
#   brew install oci-cli
#   oci setup config        ← run once to authenticate
#
# Usage:
#   bash scripts/provision-oracle.sh
#
# What it does:
#   1. Prompts for your compartment OCID and SSH public key
#   2. Finds the latest Ubuntu 22.04 ARM image in your region
#   3. Uses your default VCN/subnet (or prompts you to pick one)
#   4. Retries instance launch across all availability domains until capacity found
#   5. Opens ports 22 / 8080 / 8081 in the security list
#   6. Prints the instance IP and SSH command when done

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
prompt()  { echo -e "${CYAN}[INPUT]${NC} $*"; }

# ── Config ────────────────────────────────────────────────────────────────────

SHAPE="VM.Standard.A1.Flex"
OCPUS=4
MEMORY_GB=24
DISPLAY_NAME="alphadivision"
RETRY_INTERVAL=10   # seconds between capacity retry attempts
BOOT_VOLUME_GB=100  # free tier allows up to 200 GB total

# Ports to open in the security list
PORTS=(22 8080 8081)

# ── Preflight ─────────────────────────────────────────────────────────────────

if ! command -v oci &>/dev/null; then
    error "OCI CLI not found. Install it with:
  brew install oci-cli
Then authenticate:
  oci setup config"
fi

if [[ ! -f ~/.oci/config ]]; then
    error "OCI config not found. Run:
  oci setup config"
fi

info "OCI CLI found: $(oci --version 2>&1 | head -1)"

# ── Region ────────────────────────────────────────────────────────────────────

HOME_REGION=$(grep '^region' ~/.oci/config | head -1 | awk -F= '{print $2}' | tr -d ' ')
if [[ -z "$HOME_REGION" ]]; then
    error "Could not determine region from OCI config. Check ~/.oci/config."
fi
info "Home region (auth): $HOME_REGION"

echo ""
prompt "Target region for instance [ap-osaka-1] (Enter for default, or e.g. eu-stockholm-1):"
read -r INPUT_REGION
REGION="${INPUT_REGION:-ap-osaka-1}"
info "Instance region: $REGION"

# All OCI CLI calls below use --region $REGION to target the right region
# while auth is handled by the home region in ~/.oci/config

# ── Compartment ───────────────────────────────────────────────────────────────

TENANCY_OCID=$(grep '^tenancy' ~/.oci/config | head -1 | awk -F= '{print $2}' | tr -d ' ')

echo ""
prompt "Enter compartment OCID (press Enter to use root/tenancy: $TENANCY_OCID):"
read -r INPUT_COMPARTMENT
COMPARTMENT_OCID="${INPUT_COMPARTMENT:-$TENANCY_OCID}"
info "Compartment: $COMPARTMENT_OCID"

# ── SSH key ───────────────────────────────────────────────────────────────────

DEFAULT_KEY=""
for candidate in ~/.ssh/id_ed25519.pub ~/.ssh/id_rsa.pub; do
    if [[ -f "$candidate" ]]; then
        DEFAULT_KEY="$candidate"
        break
    fi
done

echo ""
prompt "Path to SSH public key [${DEFAULT_KEY:-none found}]:"
read -r INPUT_KEY
SSH_KEY_FILE="${INPUT_KEY:-$DEFAULT_KEY}"

[[ -f "$SSH_KEY_FILE" ]] || error "SSH public key not found at: $SSH_KEY_FILE
Generate one with: ssh-keygen -t ed25519"

info "SSH key: $SSH_KEY_FILE"

# ── Availability domains ───────────────────────────────────────────────────────

info "Fetching availability domains..."
OCI_OUT=$(oci iam availability-domain list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --query 'data[].name' \
    --raw-output 2>&1) || true

if echo "$OCI_OUT" | grep -q "NotAuthenticated\|401"; then
    error "OCI authentication failed (401).
Check that your public key is uploaded in the OCI Console:
  Profile → My profile → API keys
The key fingerprint should be: $(grep fingerprint ~/.oci/config | awk -F= '{print $2}' | tr -d ' ')"
fi

ADS=$(echo "$OCI_OUT" | tr -d '[]"' | tr ',' '\n' | tr -d ' ' | grep -v '^$' || true)

if [[ -z "$ADS" ]]; then
    error "Could not list availability domains. OCI response:
$OCI_OUT"
fi

AD_COUNT=$(echo "$ADS" | wc -l | tr -d ' ')
info "Found $AD_COUNT availability domain(s):"
echo "$ADS" | while read -r ad; do echo "    - $ad"; done

# ── Ubuntu 22.04 ARM image ────────────────────────────────────────────────────

info "Finding latest Ubuntu 22.04 ARM image..."
IMAGE_OCID=$(oci compute image list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --operating-system "Canonical Ubuntu" \
    --operating-system-version "22.04" \
    --shape "$SHAPE" \
    --sort-by TIMECREATED \
    --sort-order DESC \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || true)

if [[ -z "$IMAGE_OCID" || "$IMAGE_OCID" == "null" ]]; then
    error "Could not find Ubuntu 22.04 ARM image in region $REGION.
Try listing images manually:
  oci compute image list --compartment-id $COMPARTMENT_OCID --operating-system 'Canonical Ubuntu'"
fi
info "Image: $IMAGE_OCID"

# ── Subnet ────────────────────────────────────────────────────────────────────

info "Finding subnets..."
SUBNET_DATA=$(oci network subnet list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --query 'data[].{id:id, name:"display-name", cidr:"cidr-block"}' \
    --output table 2>/dev/null || true)

if [[ -z "$SUBNET_DATA" ]]; then
    error "No subnets found in compartment. Create a VCN with a public subnet in the OCI Console first."
fi

echo ""
echo "$SUBNET_DATA"

# Get the first subnet OCID as default
DEFAULT_SUBNET=$(oci network subnet list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || true)

echo ""
prompt "Enter subnet OCID to use [${DEFAULT_SUBNET}]:"
read -r INPUT_SUBNET
SUBNET_OCID="${INPUT_SUBNET:-$DEFAULT_SUBNET}"
info "Subnet: $SUBNET_OCID"

# ── Open ports in security list ───────────────────────────────────────────────

info "Checking security list for required ports (${PORTS[*]})..."
VCN_OCID=$(oci network subnet get \
    --subnet-id "$SUBNET_OCID" \
    --region "$REGION" \
    --query 'data."vcn-id"' \
    --raw-output 2>/dev/null || true)

SECLIST_OCID=$(oci network security-list list \
    --compartment-id "$COMPARTMENT_OCID" \
    --region "$REGION" \
    --vcn-id "$VCN_OCID" \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || true)

if [[ -n "$SECLIST_OCID" && "$SECLIST_OCID" != "null" ]]; then
    # Build ingress rules JSON for each port
    INGRESS_RULES="["
    FIRST=true
    for PORT in "${PORTS[@]}"; do
        [[ "$FIRST" == true ]] && FIRST=false || INGRESS_RULES+=","
        INGRESS_RULES+=$(cat <<JSON
{
  "isStateless": false,
  "protocol": "6",
  "source": "0.0.0.0/0",
  "sourceType": "CIDR_BLOCK",
  "tcpOptions": {
    "destinationPortRange": {"max": $PORT, "min": $PORT}
  }
}
JSON
)
    done
    INGRESS_RULES+="]"

    # Get existing rules and merge (OCI replaces the whole list)
    EXISTING_RULES=$(oci network security-list get \
        --security-list-id "$SECLIST_OCID" \
        --region "$REGION" \
        --query 'data."ingress-security-rules"' \
        2>/dev/null || echo "[]")

    # Check which ports are already open
    MISSING_PORTS=()
    for PORT in "${PORTS[@]}"; do
        if ! echo "$EXISTING_RULES" | grep -q "\"min\": $PORT"; then
            MISSING_PORTS+=("$PORT")
        fi
    done

    if [[ ${#MISSING_PORTS[@]} -gt 0 ]]; then
        info "Opening ports: ${MISSING_PORTS[*]}..."
        # Append new rules to existing ones
        MERGED=$(echo "$EXISTING_RULES" | python3 -c "
import sys, json
existing = json.load(sys.stdin)
new_ports = [${MISSING_PORTS[*]/%/, }]
new_rules = [
    {
        'isStateless': False,
        'protocol': '6',
        'source': '0.0.0.0/0',
        'sourceType': 'CIDR_BLOCK',
        'tcpOptions': {'destinationPortRange': {'max': p, 'min': p}}
    }
    for p in new_ports
]
print(json.dumps(existing + new_rules))
")
        oci network security-list update \
            --security-list-id "$SECLIST_OCID" \
            --region "$REGION" \
            --ingress-security-rules "$MERGED" \
            --force \
            --query 'data.id' \
            --raw-output &>/dev/null && info "Ports opened." || warn "Could not update security list — open ports manually in OCI Console."
    else
        info "All required ports already open."
    fi
else
    warn "Could not find security list — open ports 22, 8080, 8081 manually in OCI Console."
fi

# ── Launch instance (with retry) ──────────────────────────────────────────────

echo ""
info "Launching instance (shape: $SHAPE, ${OCPUS} OCPU / ${MEMORY_GB}GB RAM)..."
info "Will retry across all availability domains until capacity is found."
info "Press Ctrl+C to stop."
echo ""

ATTEMPT=0
INSTANCE_OCID=""

while true; do
    for AD in $ADS; do
        ATTEMPT=$((ATTEMPT + 1))
        echo -ne "${YELLOW}[ATTEMPT $ATTEMPT]${NC} Trying $AD... "

        RESULT=$(oci compute instance launch \
            --availability-domain "$AD" \
            --compartment-id "$COMPARTMENT_OCID" \
            --region "$REGION" \
            --shape "$SHAPE" \
            --shape-config "{\"ocpus\": $OCPUS, \"memoryInGBs\": $MEMORY_GB}" \
            --image-id "$IMAGE_OCID" \
            --subnet-id "$SUBNET_OCID" \
            --display-name "$DISPLAY_NAME" \
            --ssh-authorized-keys-file "$SSH_KEY_FILE" \
            --boot-volume-size-in-gbs "$BOOT_VOLUME_GB" \
            --assign-public-ip true \
            2>&1 || true)

        if echo "$RESULT" | grep -q '"lifecycle-state"'; then
            INSTANCE_OCID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])")
            echo -e "${GREEN}SUCCESS${NC}"
            break 2
        elif echo "$RESULT" | grep -qi "out of capacity\|InternalError\|LimitExceeded"; then
            echo -e "${RED}out of capacity${NC}"
        else
            # Unexpected error — print it
            echo -e "${RED}error${NC}"
            echo "$RESULT" | tail -5
        fi
    done

    echo -e "    All ADs exhausted. Retrying in ${RETRY_INTERVAL}s... (Ctrl+C to stop)"
    sleep "$RETRY_INTERVAL"
done

# ── Wait for instance to be RUNNING ───────────────────────────────────────────

info "Instance created: $INSTANCE_OCID"
info "Waiting for instance to reach RUNNING state..."

while true; do
    STATE=$(oci compute instance get \
        --instance-id "$INSTANCE_OCID" \
        --region "$REGION" \
        --query 'data."lifecycle-state"' \
        --raw-output 2>/dev/null || echo "UNKNOWN")
    echo -ne "\r    State: $STATE    "
    [[ "$STATE" == "RUNNING" ]] && break
    sleep 5
done
echo ""

# ── Get public IP ─────────────────────────────────────────────────────────────

info "Fetching public IP..."
PUBLIC_IP=$(oci compute instance list-vnics \
    --instance-id "$INSTANCE_OCID" \
    --region "$REGION" \
    --query 'data[0]."public-ip"' \
    --raw-output 2>/dev/null || true)

if [[ -z "$PUBLIC_IP" || "$PUBLIC_IP" == "null" ]]; then
    warn "Could not retrieve public IP automatically."
    warn "Find it in the OCI Console under: Compute → Instances → $DISPLAY_NAME → Instance Details"
else
    info "Public IP: $PUBLIC_IP"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Instance ready!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo ""
echo "  Instance OCID : $INSTANCE_OCID"
echo "  Public IP     : ${PUBLIC_IP:-<check OCI Console>}"
echo "  SSH key       : $SSH_KEY_FILE"
echo ""
echo "  SSH in:"
echo "    ssh -i ${SSH_KEY_FILE%.pub} ubuntu@${PUBLIC_IP:-<ip>}"
echo ""
echo "  Next steps:"
echo "    1. SSH into the instance (wait ~60s for boot)"
echo "    2. Create /opt/alphadivision/.env with your secrets"
echo "    3. Run the deploy script:"
echo "       REPO_URL=https://github.com/nickchow0/alphadivision.git sudo bash deploy.sh"
echo ""

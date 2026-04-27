# vne/terraform

Files in this directory are **generated at runtime** by the OpenTofu renderer
(`shared/renderers/opentofu.py`). Do not commit anything in here other than
this README and the `modules/opnsense-vm/` placeholder structure — the
generator writes `main.tf`, `variables.tf`, `versions.tf`, `outputs.tf`, and
`terraform.tfvars` every run.

Terraform state lives at `vne/terraform/terraform.tfstate` (local backend) and
is gitignored.

To run OpenTofu manually for debugging:

```
cd vne/terraform
TF_VAR_proxmox_endpoint=$PROXMOX_VE_ENDPOINT \
TF_VAR_proxmox_api_token=$PROXMOX_VE_API_TOKEN \
tofu apply
```

Provider versions are pinned in `shared/renderers/opentofu.py` — change them
there, not in any generated file (which gets overwritten).

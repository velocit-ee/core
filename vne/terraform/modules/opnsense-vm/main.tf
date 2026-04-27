# Placeholder for the opnsense-vm module. The current OpenTofu renderer emits
# a flat plan that does not consume this module — it lives here as scaffolding
# for users who want to fork the renderer to use a more modular layout.
#
# A future refactor will move the resource definitions in
# shared/renderers/opentofu.py:_main_tf() into this module and call it from
# main.tf with module "opnsense" { source = "./modules/opnsense-vm" ... }.

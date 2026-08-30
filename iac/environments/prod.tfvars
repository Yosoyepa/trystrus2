# prod: min 1 (no cold-start on the passkey/mandate path), REGIONAL SQL,
# deletion protection, real domain (LB + managed cert + Armor enforce).
project_id           = "trytrust"
region               = "southamerica-east1"
environment          = "prod"
image                = "southamerica-east1-docker.pkg.dev/trytrust/aval/backend:latest"
web_image            = "southamerica-east1-docker.pkg.dev/trytrust/aval/web:latest"
domain               = "trytrust.lat"
yuno_sim_url         = "https://trytrust.lat/yuno"
min_instances        = 1
max_instances        = 10
db_tier              = "db-custom-2-7680"
db_availability_type = "REGIONAL"
deletion_protection  = true
# budget_amount      = 100
# billing_account    = "XXXXXX-XXXXXX-XXXXXX"

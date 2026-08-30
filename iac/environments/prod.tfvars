# prod: min 1 (no cold-start on the passkey/mandate path), REGIONAL SQL,
# deletion protection, real domain (LB + managed cert + Armor enforce).
project_id           = "REPLACE_WITH_PROJECT_ID"
region               = "southamerica-east1"
environment          = "prod"
domain               = "trytrust.lat"
min_instances        = 1
max_instances        = 10
db_tier              = "db-custom-2-7680"
db_availability_type = "REGIONAL"
deletion_protection  = true
# budget_amount      = 100
# billing_account    = "XXXXXX-XXXXXX-XXXXXX"

# dev: cheap, no LB (domain empty → direct run.app URLs), scale to zero.
project_id  = "REPLACE_WITH_PROJECT_ID"
region      = "southamerica-east1"
environment = "dev"
# image  = "southamerica-east1-docker.pkg.dev/<PROJECT>/aval/backend@sha256:..."
# web_image = "southamerica-east1-docker.pkg.dev/<PROJECT>/aval/web@sha256:..."
domain               = ""
min_instances        = 0
max_instances        = 2
db_tier              = "db-custom-1-3840"
db_availability_type = "ZONAL"
deletion_protection  = false

# dev: cheap, no LB (domain empty → direct run.app URLs), scale to zero.
project_id                      = "trytrust"
region                          = "southamerica-east1"
environment                     = "dev"
manage_shared_project_resources = true
image                           = "southamerica-east1-docker.pkg.dev/trytrust/aval/backend:latest"
web_image                       = "southamerica-east1-docker.pkg.dev/trytrust/aval/web:latest"
domain                          = ""
min_instances                   = 0
max_instances                   = 2
db_tier                         = "db-custom-1-3840"
db_availability_type            = "ZONAL"
deletion_protection             = false

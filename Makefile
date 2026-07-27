# Standard service targets for the naustet box. Slug is this service's dir name.
SERVER ?= msge
SLUG   ?= myself

deploy:            ## Pull, rebuild, restart (run on the server in /srv/$(SLUG))
	git pull --ff-only
	./scripts/generate-page-dates.sh || echo "WARN: page dates not regenerated — pages will stamp boot time"
	docker compose up -d --build

logs:              ## Follow container logs
	docker compose logs -f --tail=100

status:            ## Show container status
	docker compose ps

verify:            ## Build + boot + hit /healthz (local smoke test)
	./scripts/generate-page-dates.sh || echo "WARN: page dates not regenerated — pages will stamp boot time"
	docker compose up -d --build
	@sleep 3
	@curl -fsS http://172.18.0.1:4024/healthz && echo "  OK" || (echo "  FAILED"; exit 1)

remote-deploy:     ## Deploy from a laptop over SSH
	ssh $(SERVER) 'cd /srv/$(SLUG) && make deploy'

.PHONY: deploy logs status verify remote-deploy

COMPOSE := docker
IMAGE_NAME = quotes_showcase
CONTAINER_NAME = quotes_showcase

build:
	$(COMPOSE) build -t $(IMAGE_NAME) .
## Запустить контейнер с пересборкой
run:
	$(COMPOSE) run -d \
		-p 127.0.0.1:8000:8000 \
		-v .:/app \
		--cpus="0.5" \
  		--memory="256mb" \
		--memory-swap="256mb" \
		--name $(CONTAINER_NAME) \
		$(IMAGE_NAME)


stats:
	$(COMPOSE) stats $(CONTAINER_NAME)
## Остановить контейнеры
down:
	$(COMPOSE) rm -f $(CONTAINER_NAME)
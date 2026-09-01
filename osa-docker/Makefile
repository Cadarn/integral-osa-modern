SHELL := /bin/bash

IMAGE_TAG ?= latest
REGISTRY ?= integralsw/osa
IMAGE_ARM64 ?= $(REGISTRY):$(IMAGE_TAG)-arm64
IMAGE_X86 ?= $(REGISTRY):$(IMAGE_TAG)-x86
IMAGE_BATCH ?= $(REGISTRY):$(IMAGE_TAG)-batch
IMAGE_MULTI ?= $(REGISTRY):$(IMAGE_TAG)

.PHONY: help build-arm64 build-x86 build-batch build-multiarch test

help:
	@echo "INTEGRAL OSA Docker Build Targets:"
	@echo "  build-arm64      - Build native ARM64 container for Apple Silicon & Graviton"
	@echo "  build-x86        - Build x86_64 container baseline"
	@echo "  build-batch      - Build lean headless container for K8s batch jobs"
	@echo "  build-multiarch  - Build and push multi-arch image via buildx"
	@echo "  test             - Run test suite inside container"

build-arm64:
	docker build --platform linux/arm64 -f Dockerfile.arm64 -t $(IMAGE_ARM64) .

build-x86:
	docker build --platform linux/amd64 -f Dockerfile.x86 -t $(IMAGE_X86) .

build-batch:
	docker build -f Dockerfile.batch -t $(IMAGE_BATCH) .

build-multiarch:
	docker buildx build --platform linux/amd64,linux/arm64 -f Dockerfile.batch -t $(IMAGE_MULTI) --push .

test:
	docker run --rm $(IMAGE_ARM64) make -C /tests || docker run --rm --platform linux/amd64 $(IMAGE_X86) make -C /tests

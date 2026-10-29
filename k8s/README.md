# Kubernetes Deployment Guide for Link Board

This directory contains complete Kubernetes manifests for all three environments:
- `k8s/qa/` (Namespace: `qa`)
- `k8s/stage/` (Namespace: `stage`)
- `k8s/prod/` (Namespace: `prod`)

## Manifest Hierarchy
Each environment folder contains:
1. `00-namespace.yaml`: Environment namespace definition.
2. `01-config.yaml`: `linkboard-config` ConfigMap, `linkboard-db-secret` Secret (credentials), `regcred` Secret (Docker Hub registry placeholder), and `postgres-migrations` ConfigMap (migration scripts).
3. `02-postgres.yaml`: PersistentVolumeClaim (`postgres-pvc`), single-replica PostgreSQL Deployment with health probes, and internal ClusterIP Service (`postgres:5432`).
4. `03-redis.yaml`: Redis Deployment with health probes and internal ClusterIP Service (`redis:6379`).
5. `04-app.yaml`: Application Deployment with `initContainers` (waits for PostgreSQL then executes migrations from `postgres-migrations`), liveness & readiness probes hitting `/health`, `imagePullPolicy: Always`, and `imagePullSecrets: [regcred]`. Exposed via a NodePort Service.

## Docker Hub Image Tags
- **QA:** `YOUR_DOCKERHUB_USERNAME/link-board:qa` (NodePort: 30080)
- **Stage:** `YOUR_DOCKERHUB_USERNAME/link-board:stage` (NodePort: 30081)
- **Production:** `YOUR_DOCKERHUB_USERNAME/link-board:prod` (NodePort: 30082, 2 replicas)

Before deploying to a cluster with private or remote images, update `YOUR_DOCKERHUB_USERNAME` with your real Docker Hub account username across the manifests.

## Configuring Image Pull Secrets (`regcred`)
To configure your real Docker Hub credentials without committing them:
```bash
kubectl create secret docker-registry regcred \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username=<YOUR_DOCKERHUB_USERNAME> \
  --docker-password=<YOUR_DOCKERHUB_TOKEN> \
  --docker-email=<YOUR_EMAIL> \
  -n <qa|stage|prod> --dry-run=client -o yaml | kubectl apply -f -
```

## Local Deployment with Minikube

1. **Start Minikube**:
   ```powershell
   minikube start
   ```

2. **(Option A) If building and testing local images directly in Minikube**:
   Point your local Docker CLI to Minikube's Docker daemon:
   ```powershell
   & minikube -p minikube docker-env --shell powershell | Invoke-Expression
   docker build -t YOUR_DOCKERHUB_USERNAME/link-board:qa .
   docker build -t YOUR_DOCKERHUB_USERNAME/link-board:stage .
   docker build -t YOUR_DOCKERHUB_USERNAME/link-board:prod .
   ```
   *(Note: When using local images built directly in Minikube, you can temporarily set `imagePullPolicy: IfNotPresent` for offline testing if images are not yet pushed to Docker Hub).*

3. **Deploy an environment (e.g. QA)**:
   ```powershell
   kubectl apply -f k8s/qa/
   ```
   Deploy stage and prod:
   ```powershell
   kubectl apply -f k8s/stage/
   kubectl apply -f k8s/prod/
   ```

4. **Verify rollout**:
   ```powershell
   kubectl get pods -n qa
   kubectl get services -n qa
   ```

5. **Access the application**:
   ```powershell
   minikube service link-board -n qa --url
   ```
   Or visit `http://<minikube-ip>:30080` in your browser.

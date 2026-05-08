# Python App — Jenkins CI/CD + ArgoCD + Kubernetes
## Complete Step-by-Step Setup Guide

---

## Architecture Overview

```
Developer Push
     │
     ▼
  GitHub Repo
     │
     ▼ (webhook / poll)
  Jenkins CI
  ├── Python Tests + Coverage
  ├── Lint + Security Scan
  ├── SonarQube Analysis
  ├── Docker Build + Push → Container Registry
  └── Update Helm values (image tag) → Git commit
                          │
                          ▼ (detects git change)
                       ArgoCD
                       ├── Dev  → K8s dev namespace
                       ├── Staging → K8s staging namespace
                       └── Prod → K8s production namespace (manual sync)
```

**Key GitOps principle:** Jenkins builds the image and commits the new image tag to Git.
ArgoCD watches Git and syncs Kubernetes to match. Jenkins never touches K8s directly (except dev convenience).

---

## Prerequisites

### Tools required on your local machine

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | 24+ | https://docker.com/desktop |
| kubectl | 1.28+ | `brew install kubectl` |
| Helm | 3.14+ | `brew install helm` |
| ArgoCD CLI | 2.10+ | `brew install argocd` |
| Python | 3.12+ | `brew install python@3.12` |
| git | any | pre-installed |

### Infrastructure required

- Kubernetes cluster (minikube for local, EKS/GKE/AKS for production)
- Docker/container registry (Docker Hub, ECR, or self-hosted)
- Jenkins server (or use Docker Compose below)
- ArgoCD installed in cluster
- SonarQube (optional but recommended)

---

## PART 1 — Local Setup & Testing

### Step 1.1 — Clone and run the Python app locally

```bash
git clone https://github.com/your-org/python-cicd-project.git
cd python-cicd-project/app

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate          # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
APP_ENV=development python app.py

# Test it
curl http://localhost:5000/health/live
curl http://localhost:5000/health/ready
curl http://localhost:5000/api/v1/version
curl -X POST http://localhost:5000/api/v1/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Item", "description": "Hello from API"}'
curl http://localhost:5000/api/v1/items
```

### Step 1.2 — Run tests locally

```bash
cd app
pytest tests/ -v --cov=app --cov-report=term-missing
# Expected: All tests pass, coverage >= 80%
```

### Step 1.3 — Build and run Docker image locally

```bash
cd app

# Build
docker build \
  --build-arg APP_VERSION=1.0.0-local \
  --build-arg GIT_COMMIT=abc123 \
  --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
  -t python-cicd-app:local \
  .

# Run
docker run -p 5000:5000 \
  -e APP_ENV=development \
  -e DATABASE_URL=sqlite:///app.db \
  python-cicd-app:local

# Test
curl http://localhost:5000/health/ready
# Expected: {"status":"UP","db":"UP",...}
```

---

## PART 2 — Kubernetes Cluster Setup

### Step 2.1 — Start a local cluster (Minikube)

```bash
# Install minikube
brew install minikube

# Start with enough resources
minikube start \
  --cpus=4 \
  --memory=8192 \
  --disk-size=30g \
  --driver=docker \
  --addons=ingress,metrics-server

# Verify
kubectl cluster-info
kubectl get nodes
# Expected: minikube   Ready   control-plane   ...
```

### Step 2.2 — Bootstrap namespaces and policies

```bash
# Create namespaces, resource quotas, network policies
kubectl apply -f k8s/bootstrap.yaml

# Verify
kubectl get namespaces
# Expected: dev, staging, production all created
```

### Step 2.3 — Create the app secrets (do this in each namespace)

```bash
# Create secrets for dev namespace
kubectl create secret generic python-app-secrets \
  --from-literal=DATABASE_URL="sqlite:///app.db" \
  --from-literal=SECRET_KEY="dev-secret-key-change-me" \
  -n dev

# Create secrets for staging
kubectl create secret generic python-app-secrets \
  --from-literal=DATABASE_URL="postgresql://user:pass@postgres-staging:5432/appdb" \
  --from-literal=SECRET_KEY="staging-secret-key-change-me" \
  -n staging

# Create secrets for production (use your real values!)
kubectl create secret generic python-app-secrets \
  --from-literal=DATABASE_URL="postgresql://user:pass@postgres-prod:5432/appdb" \
  --from-literal=SECRET_KEY="CHANGE-THIS-TO-A-REAL-RANDOM-256-BIT-KEY" \
  -n production

# Verify (values are base64-encoded, not plaintext)
kubectl get secret python-app-secrets -n dev -o yaml
```

### Step 2.4 — Create Docker registry pull secret

```bash
# Replace with your registry credentials
kubectl create secret docker-registry registry-credentials \
  --docker-server=registry.company.com \
  --docker-username=YOUR_USERNAME \
  --docker-password=YOUR_PASSWORD \
  --docker-email=ci@company.com \
  -n dev

# Repeat for staging and production namespaces
kubectl create secret docker-registry registry-credentials \
  --docker-server=registry.company.com \
  --docker-username=YOUR_USERNAME \
  --docker-password=YOUR_PASSWORD \
  -n staging

kubectl create secret docker-registry registry-credentials \
  --docker-server=registry.company.com \
  --docker-username=YOUR_USERNAME \
  --docker-password=YOUR_PASSWORD \
  -n production
```

### Step 2.5 — Test Helm chart locally

```bash
# Lint the chart
helm lint helm/python-app/ -f helm/python-app/values-dev.yaml

# Dry run to preview manifests
helm install python-cicd-app helm/python-app/ \
  --namespace dev \
  --values helm/python-app/values.yaml \
  --values helm/python-app/values-dev.yaml \
  --set image.tag=local \
  --dry-run --debug

# Actually install to dev for testing
helm install python-cicd-app helm/python-app/ \
  --namespace dev \
  --values helm/python-app/values.yaml \
  --values helm/python-app/values-dev.yaml \
  --set image.repository=python-cicd-app \
  --set image.tag=local \
  --set imagePullSecrets="" \
  --set ingress.enabled=false

# Check status
kubectl get all -n dev
kubectl get pods -n dev
kubectl logs -f deployment/python-cicd-app -n dev

# Port-forward to test
kubectl port-forward svc/python-cicd-app 8080:80 -n dev
curl http://localhost:8080/health/ready

# Clean up test install
helm uninstall python-cicd-app -n dev
```

---

## PART 3 — ArgoCD Setup

### Step 3.1 — Install ArgoCD in the cluster

```bash
# Create ArgoCD namespace
kubectl create namespace argocd

# Install ArgoCD
kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Wait for pods to be ready (takes 2-3 minutes)
kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=argocd-server \
  -n argocd \
  --timeout=300s

# Check all ArgoCD pods are running
kubectl get pods -n argocd
```

### Step 3.2 — Access ArgoCD UI

```bash
# Port-forward the ArgoCD server
kubectl port-forward svc/argocd-server -n argocd 8080:443 &

# Get the initial admin password
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d && echo ""
# Save this password!

# Open in browser: https://localhost:8080
# Username: admin
# Password: (from above)

# OR login via CLI
argocd login localhost:8080 \
  --username admin \
  --password $(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d) \
  --insecure

# IMPORTANT: Change the admin password
argocd account update-password \
  --current-password $(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d) \
  --new-password "YOUR-NEW-SECURE-PASSWORD"
```

### Step 3.3 — Connect your Git repository to ArgoCD

```bash
# Add your Git repo (use SSH key or HTTPS token)
argocd repo add https://github.com/your-org/python-cicd-project.git \
  --username your-github-username \
  --password YOUR_GITHUB_PAT_TOKEN

# Verify repo is connected
argocd repo list
# Expected: STATUS = Successful
```

### Step 3.4 — Create ArgoCD applications

```bash
# Register dev app
kubectl apply -f k8s/argocd/argocd-app-dev.yaml

# Register staging app
kubectl apply -f k8s/argocd/argocd-app-staging.yaml

# Register production app
kubectl apply -f k8s/argocd/argocd-app-prod.yaml

# Verify apps are created
argocd app list
# Expected: all 3 apps listed

# Check dev app status
argocd app get python-cicd-app-dev

# Manually trigger first sync
argocd app sync python-cicd-app-dev
argocd app wait python-cicd-app-dev --health --timeout 300
```

### Step 3.5 — Create an ArgoCD service account for Jenkins

```bash
# Create an ArgoCD API token for Jenkins (non-interactive)
argocd account generate-token --account jenkins-ci
# Save this token — you'll add it to Jenkins credentials

# If the jenkins-ci account doesn't exist, create it:
# Edit the argocd-cm configmap:
kubectl edit configmap argocd-cm -n argocd
# Add under data:
#   accounts.jenkins-ci: apiKey
#   accounts.jenkins-ci.enabled: "true"

# Grant jenkins-ci permissions to sync apps
argocd proj role create-token default jenkins-ci
# OR use the admin token for simplicity in dev
```

---

## PART 4 — Jenkins Setup

### Step 4.1 — Run Jenkins via Docker Compose (quick start)

```bash
# Create Jenkins docker-compose.yml
cat > jenkins/docker-compose.yml << 'EOF'
version: '3.8'
services:
  jenkins:
    image: jenkins/jenkins:lts-jdk17
    container_name: jenkins
    privileged: true
    user: root
    ports:
      - "8081:8080"
      - "50000:50000"
    volumes:
      - jenkins_home:/var/jenkins_home
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      JAVA_OPTS: "-Xmx2g -Xms512m"
volumes:
  jenkins_home:
EOF

# Start Jenkins
docker-compose -f jenkins/docker-compose.yml up -d

# Get initial admin password
docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```

Open http://localhost:8081 in your browser and complete the setup wizard.

### Step 4.2 — Install required Jenkins plugins

Go to **Manage Jenkins → Plugins → Available** and install:

```
✅ Pipeline
✅ Pipeline: Stage View
✅ Git
✅ GitHub Integration
✅ Docker Pipeline
✅ Kubernetes
✅ Kubernetes CLI
✅ Blue Ocean (optional, nicer UI)
✅ SonarQube Scanner
✅ Cobertura (code coverage)
✅ JUnit
✅ Slack Notification
✅ Credentials Binding
✅ AnsiColor
✅ Timestamper
✅ Build Discarder
✅ OWASP Dependency-Check (optional)
```

Restart Jenkins after installing.

### Step 4.3 — Configure Jenkins Credentials

Go to **Manage Jenkins → Credentials → System → Global credentials → Add Credential**:

| ID | Type | Description |
|----|------|-------------|
| `docker-registry-creds` | Username/Password | Docker registry login |
| `github-token` | Username/Password | GitHub username + PAT |
| `argocd-auth-token` | Secret text | ArgoCD API token from Step 3.5 |
| `sonarqube-token` | Secret text | SonarQube user token |
| `k8s-kubeconfig` | Secret file | ~/.kube/config |

### Step 4.4 — Configure SonarQube (optional)

1. Go to **Manage Jenkins → System → SonarQube servers**
2. Click **Add SonarQube**:
   - Name: `SonarQube-Server`
   - Server URL: `https://sonar.company.com`
   - Authentication token: select `sonarqube-token` credential
3. Click **Save**

### Step 4.5 — Configure Kubernetes Cloud for Jenkins agents

Go to **Manage Jenkins → Nodes and Clouds → Configure Clouds → Add a new cloud → Kubernetes**:

- Kubernetes URL: `https://kubernetes.default.svc` (if Jenkins runs in cluster)
  OR your cluster API URL
- Kubernetes Namespace: `jenkins`
- Jenkins URL: `http://jenkins.jenkins.svc.cluster.local:8080`
- Pod Label: `jenkins-agent`

Or for minikube:
```bash
# Get the cluster URL for Jenkins config
kubectl cluster-info | grep 'Kubernetes control plane'
# Use this URL in Jenkins Kubernetes cloud config
```

### Step 4.6 — Create the Pipeline Job

1. Go to Jenkins dashboard → **New Item**
2. Name: `python-cicd-app`
3. Type: **Multibranch Pipeline**
4. Click **OK**

Configure the job:
- **Branch Sources** → Add source → GitHub
  - Repository: `https://github.com/your-org/python-cicd-project.git`
  - Credentials: `github-token`
- **Build Configuration** → Mode: **by Jenkinsfile**
  - Script Path: `Jenkinsfile`
- **Scan Multibranch Pipeline Triggers** → Periodically if not otherwise run: `1 minute`
- Click **Save**

Click **Scan Multibranch Pipeline Now** to discover branches.

### Step 4.7 — Configure GitHub Webhook (for instant triggers)

In your GitHub repo:
1. Go to **Settings → Webhooks → Add webhook**
2. Payload URL: `http://YOUR_JENKINS_URL/github-webhook/`
3. Content type: `application/json`
4. Events: **Push events** + **Pull Request events**
5. Click **Add webhook**

---

## PART 5 — End-to-End Pipeline Run

### Step 5.1 — Trigger your first build

```bash
# Make a code change
cd app
echo "# test" >> app.py
git add .
git commit -m "feat: trigger first pipeline run"
git push origin develop
```

### Step 5.2 — Watch the pipeline

In Jenkins UI, watch the pipeline stages:
1. ✅ Checkout
2. ✅ Setup
3. ✅ Unit Tests → JUnit report appears
4. ✅ Lint + Security Scan (parallel)
5. ✅ SonarQube (waits for quality gate webhook)
6. ✅ Docker Build & Push → image pushed to registry
7. ✅ Update Helm Values → git commit with new tag
8. ✅ ArgoCD Sync Dev → app deployed to K8s dev namespace

### Step 5.3 — Verify the deployment

```bash
# Check pods are running
kubectl get pods -n dev
# Expected: python-cicd-app-xxx   Running

# Check deployment
kubectl get deployment -n dev
kubectl describe deployment python-cicd-app -n dev

# Check the image tag matches what Jenkins built
kubectl get deployment python-cicd-app -n dev \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
# Expected: registry.company.com/python-cicd-app:BUILD-SHA

# Port-forward and test
kubectl port-forward svc/python-cicd-app 8080:80 -n dev
curl http://localhost:8080/health/ready
curl http://localhost:8080/api/v1/version
# Expected: version matches IMAGE_TAG from Jenkins
```

### Step 5.4 — Verify ArgoCD is in sync

```bash
# CLI
argocd app get python-cicd-app-dev
# Expected: Sync Status: Synced | Health Status: Healthy

# Or in UI: https://localhost:8080
# App should show green "Healthy" and "Synced"
```

### Step 5.5 — Merge to main and deploy to staging

```bash
# Create PR and merge develop → main
git checkout main
git merge develop
git push origin main

# Jenkins detects main branch push → runs full pipeline
# → deploys to staging namespace
# → runs smoke tests
# → sends Slack notification to approve production
```

### Step 5.6 — Deploy to production

1. Jenkins pipeline pauses at **Approve Production** stage
2. A user in `release-managers` group clicks **Approve & Deploy** in Jenkins UI
3. Jenkins updates helm values with image tag
4. ArgoCD detects git change (production app has manual sync)
5. In ArgoCD UI, go to `python-cicd-app-prod` → click **Sync**
6. Or via CLI:
```bash
argocd app sync python-cicd-app-prod
argocd app wait python-cicd-app-prod --health --timeout 600
```

---

## PART 6 — Monitoring & Observability

### Step 6.1 — Install Prometheus + Grafana

```bash
# Add Helm repos
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Install kube-prometheus-stack (Prometheus + Grafana + Alertmanager)
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set grafana.adminPassword=admin123 \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false

# Wait for pods
kubectl get pods -n monitoring --watch
```

The app exposes `/metrics` in Prometheus format. The `ServiceMonitor` in the Helm chart auto-configures Prometheus to scrape it.

### Step 6.2 — Access Grafana

```bash
kubectl port-forward svc/monitoring-grafana 3000:80 -n monitoring
# Open http://localhost:3000
# Username: admin | Password: admin123

# Import a Flask dashboard:
# Dashboard ID: 11159 (Flask/Gunicorn)
```

---

## PART 7 — Troubleshooting

### Pod won't start — ImagePullBackOff

```bash
kubectl describe pod <pod-name> -n dev
# Check: registry URL, imagePullSecrets, credentials
kubectl get secret registry-credentials -n dev
```

### Pod CrashLoopBackOff

```bash
kubectl logs <pod-name> -n dev --previous
kubectl describe pod <pod-name> -n dev
# Check: environment variables, DATABASE_URL, SECRET_KEY
```

### ArgoCD shows OutOfSync

```bash
argocd app diff python-cicd-app-dev
# Shows what K8s has vs what Git says it should be
argocd app sync python-cicd-app-dev --force
```

### Jenkins can't connect to K8s (for agent pods)

```bash
# Verify Jenkins service account exists
kubectl get serviceaccount jenkins-agent -n jenkins
# Create if missing:
kubectl create serviceaccount jenkins-agent -n jenkins
kubectl create clusterrolebinding jenkins-agent \
  --clusterrole=cluster-admin \
  --serviceaccount=jenkins:jenkins-agent
```

### Docker build fails in pipeline

```bash
# Check docker socket is mounted
docker ps  # run inside the docker container in the pod
# Ensure privileged: true is set on the docker container in agent YAML
```

### SonarQube quality gate times out

1. Ensure the SonarQube webhook is configured:
   - In SonarQube: **Administration → Webhooks → Create**
   - URL: `http://JENKINS_URL/sonarqube-webhook/`
2. The `waitForQualityGate` step requires this webhook to fire

---

## PART 8 — Project File Structure

```
python-cicd-project/
├── app/
│   ├── app.py                    ← Flask application
│   ├── requirements.txt          ← Python dependencies
│   ├── Dockerfile                ← Multi-stage production Dockerfile
│   ├── pytest.ini                ← Test configuration
│   └── tests/
│       └── test_app.py           ← Unit & integration tests
├── helm/
│   └── python-app/
│       ├── Chart.yaml            ← Helm chart metadata
│       ├── values.yaml           ← Base values (production defaults)
│       ├── values-dev.yaml       ← Dev overrides
│       ├── values-staging.yaml   ← Staging overrides
│       └── templates/
│           ├── _helpers.tpl      ← Template helpers
│           ├── deployment.yaml   ← K8s Deployment
│           ├── service.yaml      ← K8s Service
│           ├── ingress.yaml      ← K8s Ingress
│           └── hpa.yaml          ← HPA + PodDisruptionBudget
├── k8s/
│   ├── bootstrap.yaml            ← Namespaces, quotas, network policies
│   └── argocd/
│       ├── argocd-app-dev.yaml   ← ArgoCD Application (dev)
│       ├── argocd-app-staging.yaml
│       └── argocd-app-prod.yaml  ← ArgoCD Application (production, manual sync)
├── jenkins/
│   └── docker-compose.yml        ← Jenkins local setup
├── Jenkinsfile                   ← CI/CD pipeline definition
└── README.md                     ← This guide
```

---

## Quick Reference — Key Commands

```bash
# ── Kubernetes ──────────────────────────────────────────────
kubectl get all -n dev
kubectl logs -f deployment/python-cicd-app -n dev
kubectl rollout history deployment/python-cicd-app -n dev
kubectl rollout undo deployment/python-cicd-app -n dev   # rollback

# ── Helm ────────────────────────────────────────────────────
helm list -A
helm upgrade python-cicd-app helm/python-app/ \
  --namespace dev -f helm/python-app/values-dev.yaml
helm rollback python-cicd-app 1 -n dev                   # rollback to revision 1

# ── ArgoCD ──────────────────────────────────────────────────
argocd app list
argocd app get python-cicd-app-dev
argocd app sync python-cicd-app-dev
argocd app rollback python-cicd-app-prod 5               # rollback to revision 5
argocd app history python-cicd-app-prod

# ── Docker ──────────────────────────────────────────────────
docker build -t python-cicd-app:test ./app
docker run --rm -p 5000:5000 python-cicd-app:test

# ── Tests ───────────────────────────────────────────────────
cd app && pytest tests/ -v --cov=app
```

---

## Interview Key Points — What This Project Demonstrates

| Concept | Implementation |
|---------|---------------|
| GitOps | Jenkins commits image tag to Git; ArgoCD syncs K8s from Git |
| Immutable artifacts | Docker image tagged with BUILD_NUMBER-GIT_SHA |
| Environment parity | Same Helm chart, different values files per env |
| Zero-downtime deploy | RollingUpdate strategy, maxUnavailable=0 |
| Security | Non-root container, readOnlyRootFilesystem, NetworkPolicy |
| Self-healing | ArgoCD selfHeal=true auto-corrects manual K8s changes |
| Observability | Prometheus metrics, structured JSON logs, health probes |
| Quality gates | Coverage threshold, SonarQube gate, security scan |
| Approval gates | Jenkins input step with RBAC (release-managers group) |
| Rollback | ArgoCD rollback via history, Helm rollback, K8s rollout undo |

# Kubernetes Logs Manager

A lightweight Streamlit dashboard to view Kubernetes pod logs from a browser.

Users can select:

- Namespace
- Pod
- Container
- Tail lines
- Previous container logs
- Filter text
- Download captured logs

## Architecture

```text
User Browser
  -> Streamlit Dashboard
  -> Kubernetes API Server
  -> Pod Logs
```

## Project Structure

```text
.
├── app.py
├── Dockerfile
├── requirements.txt
├── manifests
│   ├── namespace.yaml
│   ├── rbac.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   └── ingress.yaml
└── README.md
```

## Run Locally

You need a valid kubeconfig on your machine.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open:

```text
http://localhost:8501
```

## Build Docker Image

```bash
docker build -t k8s-logs-manager:latest .
```

For a registry:

```bash
docker tag k8s-logs-manager:latest <your-registry>/k8s-logs-manager:latest
docker push <your-registry>/k8s-logs-manager:latest
```

Update the image in:

```text
manifests/deployment.yaml
```

Replace:

```text
your-registry/k8s-logs-manager:latest
```

with your actual image.

## Deploy to Kubernetes

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/rbac.yaml
kubectl apply -f manifests/deployment.yaml
kubectl apply -f manifests/service.yaml
kubectl apply -f manifests/ingress.yaml
```

## Verify

```bash
kubectl get pods -n k8s-logs-manager
kubectl get svc -n k8s-logs-manager
kubectl get ingress -n k8s-logs-manager
```

## Port Forward Test

```bash
kubectl port-forward svc/k8s-logs-manager 8501:80 -n k8s-logs-manager
```

Then open:

```text
http://localhost:8501
```

## RBAC Notes

The default RBAC is cluster-wide read-only for namespaces, pods, and pod logs.

Allowed resources:

```text
namespaces: get, list
pods: get, list
pods/log: get, list, watch
```

For production, restrict access by namespace if required.

## Ingress Notes

Update this placeholder host in `manifests/ingress.yaml`:

```text
k8s-logs-manager.example.com
```

Use your actual internal DNS name.

## Security Recommendations

For production usage, add one of these in front of the dashboard:

- Azure AD authentication
- NGINX basic auth
- OAuth2 Proxy
- Internal-only ingress
- Namespace allowlist

## Common Issues

### Forbidden: pods/log

Check RBAC:

```bash
kubectl auth can-i get pods/log --as=system:serviceaccount:k8s-logs-manager:k8s-logs-manager-sa -A
```

### No pods visible

Check whether the ServiceAccount can list pods:

```bash
kubectl auth can-i list pods --as=system:serviceaccount:k8s-logs-manager:k8s-logs-manager-sa -A
```

### Ingress live logs stop quickly

Increase NGINX timeout annotations. This repo already includes:

```yaml
nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
```

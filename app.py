import io
import time
from datetime import datetime

import streamlit as st
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException


st.set_page_config(
    page_title="Kubernetes Logs Manager",
    page_icon="📜",
    layout="wide",
)


@st.cache_resource
def get_k8s_client():
    """Create Kubernetes CoreV1 client.

    Inside Kubernetes, it uses the pod ServiceAccount.
    During local development, it falls back to kubeconfig.
    """
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


def api_error_message(error: ApiException) -> str:
    return f"Kubernetes API error: status={error.status}, reason={error.reason}, body={error.body}"


@st.cache_data(ttl=30)
def list_namespaces():
    v1 = get_k8s_client()
    return sorted([ns.metadata.name for ns in v1.list_namespace().items])


@st.cache_data(ttl=15)
def list_pods(namespace: str):
    v1 = get_k8s_client()
    pods = v1.list_namespaced_pod(namespace=namespace).items
    return sorted([pod.metadata.name for pod in pods])


@st.cache_data(ttl=15)
def list_containers(namespace: str, pod_name: str):
    v1 = get_k8s_client()
    pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    containers = []
    if pod.spec.init_containers:
        containers.extend([container.name for container in pod.spec.init_containers])
    if pod.spec.containers:
        containers.extend([container.name for container in pod.spec.containers])
    return containers


def read_logs(namespace: str, pod_name: str, container: str, tail_lines: int, previous: bool):
    v1 = get_k8s_client()
    return v1.read_namespaced_pod_log(
        name=pod_name,
        namespace=namespace,
        container=container,
        tail_lines=tail_lines,
        previous=previous,
        timestamps=True,
    )


def stream_logs(namespace: str, pod_name: str, container: str, tail_lines: int, previous: bool, filter_text: str):
    v1 = get_k8s_client()
    log_placeholder = st.empty()
    status_placeholder = st.empty()
    buffer = io.StringIO()

    if previous:
        logs = read_logs(namespace, pod_name, container, tail_lines, previous=True)
        if filter_text:
            logs = "\n".join([line for line in logs.splitlines() if filter_text.lower() in line.lower()])
        log_placeholder.code(logs or "No previous logs found.")
        return logs

    status_placeholder.info("Streaming live logs. Refresh the page or change selection to stop.")

    stream = watch.Watch().stream(
        v1.read_namespaced_pod_log,
        name=pod_name,
        namespace=namespace,
        container=container,
        follow=True,
        tail_lines=tail_lines,
        timestamps=True,
        _preload_content=False,
    )

    try:
        for raw_line in stream:
            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
            if filter_text and filter_text.lower() not in line.lower():
                continue

            buffer.write(line)
            current_logs = buffer.getvalue()

            # Keep UI responsive by showing latest portion only.
            log_placeholder.code(current_logs[-20000:] or "Waiting for logs...")
            time.sleep(0.02)
    except Exception as exc:
        status_placeholder.error(f"Log stream stopped: {exc}")

    return buffer.getvalue()


st.title("📜 Kubernetes Logs Manager")
st.caption("Select namespace, pod, and container to view live pod logs from Kubernetes.")

with st.sidebar:
    st.header("Log Selection")

    try:
        namespaces = list_namespaces()
    except ApiException as exc:
        st.error(api_error_message(exc))
        st.stop()
    except Exception as exc:
        st.error(f"Unable to connect to Kubernetes: {exc}")
        st.stop()

    namespace = st.selectbox("Namespace", namespaces, index=0 if namespaces else None)

    if st.button("Refresh namespaces/pods"):
        st.cache_data.clear()
        st.rerun()

    pods = []
    if namespace:
        try:
            pods = list_pods(namespace)
        except ApiException as exc:
            st.error(api_error_message(exc))
            st.stop()

    pod_name = st.selectbox("Pod", pods, index=0 if pods else None)

    containers = []
    if namespace and pod_name:
        try:
            containers = list_containers(namespace, pod_name)
        except ApiException as exc:
            st.error(api_error_message(exc))
            st.stop()

    container = st.selectbox("Container", containers, index=0 if containers else None)

    tail_lines = st.number_input("Tail lines", min_value=10, max_value=5000, value=200, step=50)
    previous = st.checkbox("Show previous terminated container logs")
    filter_text = st.text_input("Filter text", placeholder="error, exception, timeout...")

    start_logs = st.button("Start Logs", type="primary")

col1, col2, col3 = st.columns(3)
col1.metric("Namespace", namespace or "-")
col2.metric("Pod", pod_name or "-")
col3.metric("Container", container or "-")

st.divider()

if not namespace or not pod_name or not container:
    st.info("Select a namespace, pod, and container from the sidebar.")
elif start_logs:
    try:
        logs = stream_logs(namespace, pod_name, container, int(tail_lines), previous, filter_text.strip())
        if logs:
            filename = f"{namespace}_{pod_name}_{container}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            st.download_button("Download captured logs", logs, file_name=filename, mime="text/plain")
    except ApiException as exc:
        st.error(api_error_message(exc))
    except Exception as exc:
        st.error(f"Unable to read logs: {exc}")
else:
    st.info("Click **Start Logs** to begin.")

import json
from datetime import datetime

import streamlit as st
from kubernetes import client, config
from kubernetes.client.rest import ApiException

st.set_page_config(page_title="Kubernetes Logs Manager", page_icon="📜", layout="wide")

TIME_WINDOWS = {
    "Last 1 min": 60,
    "Last 5 mins": 300,
    "Last 10 mins": 600,
    "Last 30 mins": 1800,
    "Last 1 hour": 3600,
}


@st.cache_resource
def get_k8s_client():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


def api_error_message(error):
    return f"Kubernetes API error: status={error.status}, reason={error.reason}, body={error.body}"


@st.cache_data(ttl=30)
def list_namespaces():
    v1 = get_k8s_client()
    return sorted([ns.metadata.name for ns in v1.list_namespace().items])


@st.cache_data(ttl=15)
def list_pods(namespace):
    v1 = get_k8s_client()
    return sorted([pod.metadata.name for pod in v1.list_namespaced_pod(namespace=namespace).items])


@st.cache_data(ttl=15)
def list_containers(namespace, pod_name):
    v1 = get_k8s_client()
    pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    containers = []
    if pod.spec.init_containers:
        containers.extend([container.name for container in pod.spec.init_containers])
    if pod.spec.containers:
        containers.extend([container.name for container in pod.spec.containers])
    return containers


def read_logs(namespace, pod_name, container, tail_lines, previous, since_seconds):
    v1 = get_k8s_client()
    return v1.read_namespaced_pod_log(
        name=pod_name,
        namespace=namespace,
        container=container,
        tail_lines=tail_lines,
        previous=previous,
        since_seconds=since_seconds,
        timestamps=True,
    )


def filter_lines(logs, filter_text):
    lines = logs.splitlines()
    if not filter_text:
        return lines
    keyword = filter_text.lower()
    return [line for line in lines if keyword in line.lower()]


def split_k8s_timestamp(line):
    if " " not in line:
        return "", line
    first, rest = line.split(" ", 1)
    if "T" in first and first.endswith("Z"):
        return first, rest
    return "", line


def parse_json_lines(lines):
    parsed = []
    invalid = []
    for line in lines:
        timestamp, message = split_k8s_timestamp(line)
        try:
            data = json.loads(message.strip())
            if not isinstance(data, dict):
                data = {"message": data}
            if timestamp:
                data = {"timestamp": timestamp, **data}
            parsed.append(data)
        except json.JSONDecodeError:
            invalid.append(line)
    return parsed, invalid


def render_logs(lines, display_format):
    if not lines:
        st.warning("No logs found for the selected time range or filter.")
        return

    if display_format == "JSON":
        parsed, invalid = parse_json_lines(lines)
        if parsed:
            st.json(parsed, expanded=False)
        if invalid:
            st.warning(f"{len(invalid)} lines are not valid JSON. Showing them as normal logs below.")
            st.code("\n".join(invalid), language="text")
        return

    if display_format == "List":
        for index, line in enumerate(lines, start=1):
            st.text(f"{index}. {line}")
        return

    st.code("\n".join(lines), language="text")


st.title("📜 Kubernetes Logs Manager")
st.caption("Select namespace, pod, container and time range to view pod logs.")

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
    time_window_label = st.selectbox("Time range", list(TIME_WINDOWS.keys()), index=1)
    tail_lines = st.number_input("Max lines", min_value=10, max_value=10000, value=500, step=50)
    display_format = st.radio("Display format", ["Normal", "List", "JSON"], horizontal=True)
    previous = st.checkbox("Show previous terminated container logs")
    filter_text = st.text_input("Filter text", placeholder="error, exception, timeout...")
    fetch_logs = st.button("Fetch Logs", type="primary")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Namespace", namespace or "-")
col2.metric("Pod", pod_name or "-")
col3.metric("Container", container or "-")
col4.metric("Time Range", time_window_label)

st.divider()

if not namespace or not pod_name or not container:
    st.info("Select a namespace, pod, and container from the sidebar.")
elif fetch_logs:
    try:
        logs = read_logs(
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=int(tail_lines),
            previous=previous,
            since_seconds=TIME_WINDOWS[time_window_label],
        )
        lines = filter_lines(logs, filter_text.strip())
        render_logs(lines, display_format)

        if lines:
            filename = f"{namespace}_{pod_name}_{container}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            st.download_button("Download logs", "\n".join(lines), file_name=filename, mime="text/plain")
    except ApiException as exc:
        st.error(api_error_message(exc))
    except Exception as exc:
        st.error(f"Unable to read logs: {exc}")
else:
    st.info("Click **Fetch Logs** to load logs. The page will not auto-refresh.")

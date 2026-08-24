import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    "Last 6 hours": 21600,
    "Last 12 hours": 43200,
    "Last 24 hours": 86400,
    "Last 3 days": 259200,
    "Last 7 days": 604800,
}

fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)


def get_allowed_namespaces():
    raw_value = os.getenv("ALLOWED_NAMESPACES", "").strip()
    if not raw_value:
        return []
    return sorted([item.strip() for item in raw_value.split(",") if item.strip()])


def get_log_timezone():
    timezone_name = os.getenv("LOG_TIMEZONE", "Asia/Dubai").strip() or "Asia/Dubai"
    try:
        return timezone_name, ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return "UTC", timezone.utc


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
    cluster_namespaces = sorted([ns.metadata.name for ns in v1.list_namespace().items])
    allowed_namespaces = get_allowed_namespaces()
    if not allowed_namespaces:
        return cluster_namespaces
    return [namespace for namespace in cluster_namespaces if namespace in allowed_namespaces]


@st.cache_data(ttl=15)
def list_pods(namespace):
    v1 = get_k8s_client()
    return sorted([pod.metadata.name for pod in v1.list_namespaced_pod(namespace=namespace).items])


@st.cache_data(ttl=10)
def get_pod_details(namespace, pod_name):
    return get_k8s_client().read_namespaced_pod(name=pod_name, namespace=namespace)


@st.cache_data(ttl=15)
def list_containers(namespace, pod_name):
    pod = get_pod_details(namespace, pod_name)
    containers = []
    if pod.spec.init_containers:
        containers.extend([container.name for container in pod.spec.init_containers])
    if pod.spec.containers:
        containers.extend([container.name for container in pod.spec.containers])
    return containers


def get_container_restart_count(namespace, pod_name, container):
    pod = get_pod_details(namespace, pod_name)
    statuses = (pod.status.init_container_statuses or []) + (pod.status.container_statuses or [])
    for status in statuses:
        if status.name == container:
            return status.restart_count or 0
    return 0


def read_logs(namespace, pod_name, container, tail_lines, previous, since_seconds=None):
    v1 = get_k8s_client()
    kwargs = {
        "name": pod_name,
        "namespace": namespace,
        "container": container,
        "tail_lines": tail_lines,
        "previous": previous,
        "timestamps": True,
    }
    if since_seconds is not None:
        kwargs["since_seconds"] = max(1, int(since_seconds))
    return v1.read_namespaced_pod_log(**kwargs)


def split_k8s_timestamp(line):
    if " " not in line:
        return "", line
    first, rest = line.split(" ", 1)
    if "T" in first and (first.endswith("Z") or "+" in first):
        return first, rest
    return "", line


def parse_timestamp(timestamp_value):
    if not timestamp_value:
        return None
    try:
        return datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_lines_by_end_time(lines, end_time_utc):
    filtered = []
    for line in lines:
        timestamp_value, _ = split_k8s_timestamp(line)
        log_time = parse_timestamp(timestamp_value)
        if log_time is None or log_time <= end_time_utc:
            filtered.append(line)
    return filtered


def filter_lines_by_text(lines, filter_text):
    if not filter_text:
        return lines
    keyword = filter_text.lower()
    return [line for line in lines if keyword in line.lower()]


def parse_json_lines(lines):
    parsed = []
    invalid = []
    for line in lines:
        timestamp_value, message = split_k8s_timestamp(line)
        try:
            data = json.loads(message.strip())
            if not isinstance(data, dict):
                data = {"message": data}
            if timestamp_value:
                data = {"timestamp": timestamp_value, **data}
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
            st.info(f"{len(invalid)} lines are plain-text logs and are shown below.")
            st.code("\n".join(invalid), language="text")
        return
    if display_format == "List":
        for index, line in enumerate(lines, start=1):
            st.text(f"{index}. {line}")
        return
    st.code("\n".join(lines), language="text")


def render_download(lines, namespace, pod_name, container, source="current"):
    if not lines:
        return
    filename = f"{namespace}_{pod_name}_{container}_{source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    st.download_button("Download logs", "\n".join(lines), file_name=filename, mime="text/plain")


def _live_logs_panel(namespace, pod_name, container, tail_lines, display_format, filter_text):
    try:
        logs = read_logs(namespace, pod_name, container, int(tail_lines), False, since_seconds=60)
        lines = filter_lines_by_text(logs.splitlines(), filter_text.strip())
        status_col, count_col = st.columns([3, 1])
        status_col.success("● Live — current container logs, refreshing every 2 seconds")
        count_col.metric("Visible lines", len(lines))
        render_logs(lines, display_format)
        render_download(lines, namespace, pod_name, container, "current")
    except ApiException as exc:
        st.error(api_error_message(exc))
    except Exception as exc:
        st.error(f"Unable to read live logs: {exc}")


if fragment is not None:
    live_logs_panel = fragment(run_every=2)(_live_logs_panel)
else:
    live_logs_panel = _live_logs_panel


st.title("📜 Kubernetes Logs Manager")
st.caption("Select namespace, pod, container and time range to view pod logs.")

allowed_namespaces = get_allowed_namespaces()
log_timezone_name, log_timezone = get_log_timezone()

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

    if allowed_namespaces:
        st.caption("Namespace allowlist is enabled.")
    if not namespaces:
        st.error("No namespaces are available. Check ALLOWED_NAMESPACES or RBAC access.")
        st.stop()

    namespace = st.selectbox("Namespace", namespaces, index=0)
    if st.button("Refresh namespaces/pods"):
        st.cache_data.clear()
        st.rerun()

    try:
        pods = list_pods(namespace) if namespace else []
    except ApiException as exc:
        st.error(api_error_message(exc))
        st.stop()
    pod_name = st.selectbox("Pod", pods, index=0 if pods else None)

    try:
        containers = list_containers(namespace, pod_name) if namespace and pod_name else []
    except ApiException as exc:
        st.error(api_error_message(exc))
        st.stop()
    container = st.selectbox("Container", containers, index=0 if containers else None)

    restart_count = 0
    if namespace and pod_name and container:
        try:
            restart_count = get_container_restart_count(namespace, pod_name, container)
        except ApiException:
            restart_count = 0
        st.caption(f"Container restart count: {restart_count}")

    time_options = ["Live Logs"] + list(TIME_WINDOWS.keys()) + ["Specific Time Range", "Custom Date & Time"]
    time_window_label = st.selectbox("Time range", time_options, index=0)

    custom_start_utc = None
    custom_end_utc = None
    now_local = datetime.now(log_timezone)

    if time_window_label == "Specific Time Range":
        st.caption(f"Select a time window in {log_timezone_name}.")
        selected_date = st.date_input("Date", value=now_local.date())
        from_time = st.time_input("From time", value=(now_local - timedelta(hours=1)).time().replace(microsecond=0))
        to_time = st.time_input("To time", value=now_local.time().replace(microsecond=0))
        custom_start_local = datetime.combine(selected_date, from_time, tzinfo=log_timezone)
        custom_end_local = datetime.combine(selected_date, to_time, tzinfo=log_timezone)
        custom_start_utc = custom_start_local.astimezone(timezone.utc)
        custom_end_utc = custom_end_local.astimezone(timezone.utc)

    elif time_window_label == "Custom Date & Time":
        default_start = now_local - timedelta(hours=1)
        st.caption(f"Custom range uses {log_timezone_name}.")
        from_date = st.date_input("From date", value=default_start.date())
        from_time = st.time_input("From time", value=default_start.time().replace(microsecond=0))
        to_date = st.date_input("To date", value=now_local.date())
        to_time = st.time_input("To time", value=now_local.time().replace(microsecond=0))
        custom_start_local = datetime.combine(from_date, from_time, tzinfo=log_timezone)
        custom_end_local = datetime.combine(to_date, to_time, tzinfo=log_timezone)
        custom_start_utc = custom_start_local.astimezone(timezone.utc)
        custom_end_utc = custom_end_local.astimezone(timezone.utc)

    tail_lines = st.number_input("Max lines", min_value=10, max_value=50000, value=5000, step=100)
    display_format = st.radio("Display format", ["Normal", "List", "JSON"], horizontal=True)

    if time_window_label != "Live Logs":
        log_source = st.radio(
            "Log source",
            ["Current container", "Previous container"],
            index=0,
            help="Previous container returns the immediately previous terminated instance of this container in the same pod.",
        )
        previous = log_source == "Previous container"
        if previous and restart_count == 0:
            st.warning("This container has no recorded restarts, so previous logs are unlikely to be available.")
        elif previous:
            st.caption("Previous logs are only for the immediately previous container instance in this same pod.")
        fetch_logs = st.button("Fetch Logs", type="primary")
    else:
        log_source = "Current container"
        previous = False
        fetch_logs = False
        st.caption("Live mode displays current-container logs in a rolling 60-second window.")

    filter_text = st.text_input("Filter text", placeholder="error, exception, timeout...")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Namespace", namespace or "-")
col2.metric("Pod", pod_name or "-")
col3.metric("Container", container or "-")
col4.metric("Restarts", restart_count)
col5.metric("Time Range", time_window_label)

st.divider()

if not namespace or not pod_name or not container:
    st.info("Select a namespace, pod, and container from the sidebar.")
elif time_window_label == "Live Logs":
    if fragment is None:
        st.warning("This Streamlit version does not support fragments, so live logs will not auto-refresh. Upgrade Streamlit to enable live refresh.")
    live_logs_panel(namespace, pod_name, container, int(tail_lines), display_format, filter_text)
elif fetch_logs:
    try:
        if previous and restart_count == 0:
            st.warning("No previous container instance is recorded for this pod/container.")
            st.stop()

        if time_window_label in ["Specific Time Range", "Custom Date & Time"]:
            if custom_start_utc >= custom_end_utc:
                st.error("From time must be earlier than To time.")
                st.stop()
            if custom_end_utc > datetime.now(timezone.utc):
                st.error("To time cannot be in the future.")
                st.stop()

            since_seconds = int((datetime.now(timezone.utc) - custom_start_utc).total_seconds())
            logs = read_logs(
                namespace=namespace,
                pod_name=pod_name,
                container=container,
                tail_lines=int(tail_lines),
                previous=previous,
                since_seconds=since_seconds,
            )
            lines = filter_lines_by_end_time(logs.splitlines(), custom_end_utc)
        else:
            logs = read_logs(
                namespace=namespace,
                pod_name=pod_name,
                container=container,
                tail_lines=int(tail_lines),
                previous=previous,
                since_seconds=TIME_WINDOWS[time_window_label],
            )
            lines = logs.splitlines()

        lines = filter_lines_by_text(lines, filter_text.strip())

        source_label = "Previous container" if previous else "Current container"
        st.subheader(f"{source_label} logs")
        if previous:
            st.info(
                "These logs belong to the immediately previous terminated container instance in the selected pod. "
                "They are not logs from pods deleted by an older Deployment rollout."
            )

        render_logs(lines, display_format)
        if lines:
            st.caption(
                f"Showing {len(lines)} log lines. Kubernetes may return fewer logs if older logs were rotated "
                "or the selected pod did not exist for the full period."
            )
            render_download(lines, namespace, pod_name, container, "previous" if previous else "current")
    except ApiException as exc:
        if previous and exc.status == 400:
            st.error("Previous container logs are not available for this pod/container. Kubernetes only retains the immediately previous terminated container instance when available.")
        else:
            st.error(api_error_message(exc))
    except Exception as exc:
        st.error(f"Unable to read logs: {exc}")
else:
    st.info("Click **Fetch Logs** to load logs. The page will not auto-refresh.")

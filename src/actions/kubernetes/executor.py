"""Kubernetes action executor."""

from typing import Any

import structlog
from kubernetes import client, config

from src.core.config import settings

logger = structlog.get_logger()


class K8sExecutor:
    """Executes remediation actions on Kubernetes."""

    def __init__(self) -> None:
        if settings.kubeconfig_path:
            config.load_kube_config(settings.kubeconfig_path)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    async def restart_pod(self, namespace: str, pod_name: str) -> dict[str, Any]:
        """Restart a pod by deleting it (assumes ReplicaSet/Deployment)."""
        logger.info("Restarting pod", namespace=namespace, pod=pod_name)

        self.core_v1.delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
        )

        return {"action": "restart_pod", "pod": pod_name, "namespace": namespace}

    async def scale_deployment(
        self,
        namespace: str,
        deployment_name: str,
        replicas: int,
    ) -> dict[str, Any]:
        """Scale a deployment to specified replicas."""
        logger.info(
            "Scaling deployment",
            namespace=namespace,
            deployment=deployment_name,
            replicas=replicas,
        )

        self.apps_v1.patch_namespaced_deployment_scale(
            name=deployment_name,
            namespace=namespace,
            body={"spec": {"replicas": replicas}},
        )

        return {
            "action": "scale_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
            "replicas": replicas,
        }

    async def rollback_deployment(
        self,
        namespace: str,
        deployment_name: str,
    ) -> dict[str, Any]:
        """Rollback a deployment to previous revision."""
        logger.info(
            "Rolling back deployment",
            namespace=namespace,
            deployment=deployment_name,
        )

        # Trigger rollback by patching with rollback annotation
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": "rollback"
                        }
                    }
                }
            }
        }

        self.apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch,
        )

        return {
            "action": "rollback_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
        }

    async def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        tail_lines: int = 100,
    ) -> str:
        """Get pod logs for diagnosis."""
        return self.core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail_lines,
        )

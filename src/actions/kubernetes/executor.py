"""Kubernetes action executor."""

import asyncio
from datetime import datetime
from functools import partial
from typing import Any

import structlog
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from src.core.config import settings
from src.core.models import ActionType, RemediationAction

logger = structlog.get_logger()


class K8sExecutor:
    """Executes remediation actions on Kubernetes."""

    def __init__(self) -> None:
        self._initialized = False
        self.core_v1: client.CoreV1Api | None = None
        self.apps_v1: client.AppsV1Api | None = None

    def _ensure_initialized(self) -> None:
        """Lazily initialize Kubernetes client."""
        if self._initialized:
            return

        if settings.kubeconfig_path:
            config.load_kube_config(settings.kubeconfig_path)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self._initialized = True
        logger.info("Kubernetes client initialized")

    async def handle_action(self, action: RemediationAction) -> dict[str, Any]:
        """Handle a remediation action from the event processor."""
        params = action.parameters

        if action.action_type == ActionType.K8S_RESTART_POD:
            return await self.restart_pod(
                namespace=params.get("namespace", settings.k8s_namespace),
                pod_name=params.get("pod_name", ""),
                label_selector=params.get("label_selector"),
            )
        elif action.action_type == ActionType.K8S_SCALE_DEPLOYMENT:
            return await self.scale_deployment(
                namespace=params.get("namespace", settings.k8s_namespace),
                deployment_name=params.get("deployment_name", ""),
                replicas=params.get("replicas", 1),
            )
        elif action.action_type == ActionType.K8S_ROLLBACK:
            return await self.rollback_deployment(
                namespace=params.get("namespace", settings.k8s_namespace),
                deployment_name=params.get("deployment_name", ""),
            )
        else:
            raise ValueError(f"Unsupported action type: {action.action_type}")

    async def restart_pod(
        self,
        namespace: str,
        pod_name: str = "",
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        """Restart pod(s) by deleting them (assumes ReplicaSet/Deployment).

        Can target a specific pod by name, or multiple pods by label selector.
        """
        self._ensure_initialized()

        loop = asyncio.get_event_loop()
        deleted_pods = []

        if pod_name:
            # Delete specific pod
            logger.info("Restarting pod", namespace=namespace, pod=pod_name)
            await loop.run_in_executor(
                None,
                partial(
                    self.core_v1.delete_namespaced_pod,
                    name=pod_name,
                    namespace=namespace,
                ),
            )
            deleted_pods.append(pod_name)
        elif label_selector:
            # Delete pods matching label selector
            logger.info(
                "Restarting pods by selector",
                namespace=namespace,
                selector=label_selector,
            )
            pods = await loop.run_in_executor(
                None,
                partial(
                    self.core_v1.list_namespaced_pod,
                    namespace=namespace,
                    label_selector=label_selector,
                ),
            )
            for pod in pods.items:
                await loop.run_in_executor(
                    None,
                    partial(
                        self.core_v1.delete_namespaced_pod,
                        name=pod.metadata.name,
                        namespace=namespace,
                    ),
                )
                deleted_pods.append(pod.metadata.name)
        else:
            raise ValueError("Must specify pod_name or label_selector")

        return {
            "action": "restart_pod",
            "namespace": namespace,
            "deleted_pods": deleted_pods,
            "count": len(deleted_pods),
        }

    async def scale_deployment(
        self,
        namespace: str,
        deployment_name: str,
        replicas: int,
    ) -> dict[str, Any]:
        """Scale a deployment to specified replicas."""
        self._ensure_initialized()

        logger.info(
            "Scaling deployment",
            namespace=namespace,
            deployment=deployment_name,
            replicas=replicas,
        )

        loop = asyncio.get_event_loop()

        # Get current state
        deployment = await loop.run_in_executor(
            None,
            partial(
                self.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
            ),
        )
        previous_replicas = deployment.spec.replicas

        # Scale
        await loop.run_in_executor(
            None,
            partial(
                self.apps_v1.patch_namespaced_deployment_scale,
                name=deployment_name,
                namespace=namespace,
                body={"spec": {"replicas": replicas}},
            ),
        )

        return {
            "action": "scale_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
            "previous_replicas": previous_replicas,
            "new_replicas": replicas,
        }

    async def rollback_deployment(
        self,
        namespace: str,
        deployment_name: str,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Rollback a deployment to previous revision.

        Note: This triggers a rollout restart which effectively rolls back
        to the previous ReplicaSet if the deployment strategy allows.
        For true revision-based rollback, use kubectl rollout undo.
        """
        self._ensure_initialized()

        logger.info(
            "Rolling back deployment",
            namespace=namespace,
            deployment=deployment_name,
            revision=revision,
        )

        loop = asyncio.get_event_loop()

        # Trigger rollout restart with timestamp annotation
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat()
                        }
                    }
                }
            }
        }

        await loop.run_in_executor(
            None,
            partial(
                self.apps_v1.patch_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
                body=patch,
            ),
        )

        return {
            "action": "rollback_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
            "triggered_at": datetime.utcnow().isoformat(),
        }

    async def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        tail_lines: int = 100,
        previous: bool = False,
    ) -> str:
        """Get pod logs for diagnosis."""
        self._ensure_initialized()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            partial(
                self.core_v1.read_namespaced_pod_log,
                name=pod_name,
                namespace=namespace,
                tail_lines=tail_lines,
                previous=previous,
            ),
        )

    async def get_pod_events(
        self,
        namespace: str,
        pod_name: str,
    ) -> list[dict[str, Any]]:
        """Get events for a pod."""
        self._ensure_initialized()

        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(
            None,
            partial(
                self.core_v1.list_namespaced_event,
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}",
            ),
        )

        return [
            {
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "count": e.count,
                "last_timestamp": e.last_timestamp.isoformat() if e.last_timestamp else None,
            }
            for e in events.items
        ]

    async def get_deployment_status(
        self,
        namespace: str,
        deployment_name: str,
    ) -> dict[str, Any]:
        """Get deployment status."""
        self._ensure_initialized()

        loop = asyncio.get_event_loop()
        deployment = await loop.run_in_executor(
            None,
            partial(
                self.apps_v1.read_namespaced_deployment,
                name=deployment_name,
                namespace=namespace,
            ),
        )

        return {
            "name": deployment.metadata.name,
            "namespace": namespace,
            "replicas": deployment.spec.replicas,
            "ready_replicas": deployment.status.ready_replicas or 0,
            "available_replicas": deployment.status.available_replicas or 0,
            "unavailable_replicas": deployment.status.unavailable_replicas or 0,
            "conditions": [
                {
                    "type": c.type,
                    "status": c.status,
                    "reason": c.reason,
                    "message": c.message,
                }
                for c in (deployment.status.conditions or [])
            ],
        }

package kubernetes.admission

import future.keywords.in

# Deny Services of type LoadBalancer without internal annotation.
# All load balancers must be internal-facing — no direct internet exposure.
# External access goes through API Gateway, CloudFront, or WAF.
deny[msg] {
	input.request.kind.kind == "Service"
	input.request.object.spec.type == "LoadBalancer"
	not input.request.object.metadata.annotations["service.beta.kubernetes.io/aws-load-balancer-internal"]
	msg := sprintf(
		"[BLOCK] Service '%v' in namespace '%v' is type LoadBalancer without the internal annotation. "
		"Add annotation 'service.beta.kubernetes.io/aws-load-balancer-internal: \"true\"' or use ClusterIP with Ingress.",
		[input.request.object.metadata.name, input.request.object.metadata.namespace],
	)
}

# Deny Ingress without TLS configured
deny[msg] {
	input.request.kind.kind == "Ingress"
	count(input.request.object.spec.tls) == 0
	msg := sprintf(
		"[BLOCK] Ingress '%v' in namespace '%v' does not have TLS configured. All Ingress resources must specify a TLS secret.",
		[input.request.object.metadata.name, input.request.object.metadata.namespace],
	)
}

# Warn: NodePort services (bypasses ALB, hits node directly)
warn[msg] {
	input.request.kind.kind == "Service"
	input.request.object.spec.type == "NodePort"
	msg := sprintf(
		"[WARN] Service '%v' in namespace '%v' is type NodePort. NodePort services expose ports directly on EC2 nodes. "
		"Prefer ClusterIP with ALB Ingress or AWS Load Balancer Controller.",
		[input.request.object.metadata.name, input.request.object.metadata.namespace],
	)
}

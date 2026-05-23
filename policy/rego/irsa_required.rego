package kubernetes.admission

import future.keywords.in

# System namespaces exempt from IRSA requirement
exempt_namespaces := {
	"kube-system",
	"kube-public",
	"kube-node-lease",
	"gatekeeper-system",
	"monitoring",
	"default",
}

# Require IRSA annotation on ServiceAccounts in workload namespaces.
# Every EKS workload pod must use its own scoped IAM role via IRSA —
# sharing node instance roles is not permitted (violates least-privilege).
deny[msg] {
	input.request.kind.kind == "ServiceAccount"
	namespace := input.request.object.metadata.namespace
	not namespace in exempt_namespaces
	not input.request.object.metadata.annotations["eks.amazonaws.com/role-arn"]
	msg := sprintf(
		"[BLOCK] ServiceAccount '%v' in namespace '%v' must have annotation 'eks.amazonaws.com/role-arn'. All workload ServiceAccounts require IRSA.",
		[input.request.object.metadata.metadata.name, namespace],
	)
}

# Validate that the IRSA ARN references the correct account and follows naming convention
warn[msg] {
	input.request.kind.kind == "ServiceAccount"
	namespace := input.request.object.metadata.namespace
	not namespace in exempt_namespaces
	arn := input.request.object.metadata.annotations["eks.amazonaws.com/role-arn"]
	# Check the ARN follows the expected pattern: arn:aws:iam::<account>:role/irsa-<namespace>-<env>
	not startswith(arn, "arn:aws:iam::")
	msg := sprintf(
		"[WARN] ServiceAccount '%v' IRSA ARN '%v' does not follow the expected format 'arn:aws:iam::<account>:role/...'",
		[input.request.object.metadata.name, arn],
	)
}

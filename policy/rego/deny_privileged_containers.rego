package kubernetes.admission

import future.keywords.in

# Deny privileged containers — PCI-DSS requirement
# Privileged containers have full access to the host and can escape the container sandbox.
deny[msg] {
	input.request.kind.kind == "Pod"
	container := input.request.object.spec.containers[_]
	container.securityContext.privileged == true
	msg := sprintf(
		"[BLOCK] Privileged containers are not permitted. Container '%v' in pod '%v' sets privileged: true. Set securityContext.privileged: false.",
		[container.name, input.request.object.metadata.name],
	)
}

# Deny init containers running as privileged
deny[msg] {
	input.request.kind.kind == "Pod"
	container := input.request.object.spec.initContainers[_]
	container.securityContext.privileged == true
	msg := sprintf(
		"[BLOCK] Privileged init containers are not permitted. Init container '%v' in pod '%v' sets privileged: true.",
		[container.name, input.request.object.metadata.name],
	)
}

# Deny containers running as root (UID 0)
deny[msg] {
	input.request.kind.kind == "Pod"
	container := input.request.object.spec.containers[_]
	container.securityContext.runAsUser == 0
	msg := sprintf(
		"[BLOCK] Containers must not run as root (UID 0). Container '%v' sets runAsUser: 0. Use a non-root UID.",
		[container.name],
	)
}

# Deny runAsNonRoot: false
deny[msg] {
	input.request.kind.kind == "Pod"
	container := input.request.object.spec.containers[_]
	container.securityContext.runAsNonRoot == false
	msg := sprintf(
		"[BLOCK] Container '%v' sets runAsNonRoot: false. Set securityContext.runAsNonRoot: true.",
		[container.name],
	)
}

# Deny allowPrivilegeEscalation: true
deny[msg] {
	input.request.kind.kind == "Pod"
	container := input.request.object.spec.containers[_]
	container.securityContext.allowPrivilegeEscalation == true
	msg := sprintf(
		"[BLOCK] Container '%v' sets allowPrivilegeEscalation: true. Set securityContext.allowPrivilegeEscalation: false.",
		[container.name],
	)
}

# Deny host PID namespace sharing
deny[msg] {
	input.request.kind.kind == "Pod"
	input.request.object.spec.hostPID == true
	msg := sprintf(
		"[BLOCK] Pod '%v' sets hostPID: true. Host PID namespace sharing is not permitted.",
		[input.request.object.metadata.name],
	)
}

# Deny host network
deny[msg] {
	input.request.kind.kind == "Pod"
	input.request.object.spec.hostNetwork == true
	msg := sprintf(
		"[BLOCK] Pod '%v' sets hostNetwork: true. Host network access is not permitted for workload pods.",
		[input.request.object.metadata.name],
	)
}

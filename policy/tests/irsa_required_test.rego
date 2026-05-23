package kubernetes.admission_test

import data.kubernetes.admission
import future.keywords.in

# ── IRSA Tests ─────────────────────────────────────────────────────────────

test_deny_serviceaccount_without_irsa_annotation {
	# ServiceAccount in a workload namespace without IRSA annotation → BLOCK
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "ServiceAccount"},
			"object": {
				"metadata": {
					"name": "payments-api",
					"namespace": "payments",
					"annotations": {},
				},
			},
		},
	}
	count(msgs) == 1
	msg := msgs[_]
	contains(msg, "must have annotation")
}

test_allow_serviceaccount_with_irsa_annotation {
	# ServiceAccount with IRSA annotation → allow
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "ServiceAccount"},
			"object": {
				"metadata": {
					"name": "payments-api",
					"namespace": "payments",
					"annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/irsa-payments-api-prod"},
				},
			},
		},
	}
	count(msgs) == 0
}

test_allow_serviceaccount_in_kube_system {
	# kube-system ServiceAccounts are exempt
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "ServiceAccount"},
			"object": {
				"metadata": {
					"name": "kube-dns",
					"namespace": "kube-system",
					"annotations": {},
				},
			},
		},
	}
	count(msgs) == 0
}

test_allow_serviceaccount_in_monitoring {
	# monitoring namespace is exempt
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "ServiceAccount"},
			"object": {
				"metadata": {
					"name": "otel-collector",
					"namespace": "monitoring",
					"annotations": {},
				},
			},
		},
	}
	count(msgs) == 0
}

# ── Privileged Container Tests ─────────────────────────────────────────────

test_deny_privileged_container {
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "Pod"},
			"object": {
				"metadata": {"name": "bad-pod"},
				"spec": {
					"containers": [{
						"name": "app",
						"image": "nginx:latest",
						"securityContext": {"privileged": true},
					}],
				},
			},
		},
	}
	count(msgs) > 0
	msg := msgs[_]
	contains(msg, "Privileged containers")
}

test_allow_non_privileged_container {
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "Pod"},
			"object": {
				"metadata": {"name": "good-pod"},
				"spec": {
					"containers": [{
						"name": "app",
						"image": "nginx:latest",
						"securityContext": {
							"privileged": false,
							"runAsNonRoot": true,
							"runAsUser": 1000,
							"allowPrivilegeEscalation": false,
						},
					}],
				},
			},
		},
	}
	count(msgs) == 0
}

test_deny_root_container {
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "Pod"},
			"object": {
				"metadata": {"name": "root-pod"},
				"spec": {
					"containers": [{
						"name": "app",
						"image": "some-image",
						"securityContext": {"runAsUser": 0},
					}],
				},
			},
		},
	}
	count(msgs) > 0
}

# ── Public Endpoint Tests ──────────────────────────────────────────────────

test_deny_public_loadbalancer {
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "Service"},
			"object": {
				"metadata": {
					"name": "my-service",
					"namespace": "payments",
					"annotations": {},
				},
				"spec": {"type": "LoadBalancer"},
			},
		},
	}
	count(msgs) > 0
	msg := msgs[_]
	contains(msg, "internal annotation")
}

test_allow_internal_loadbalancer {
	msgs := admission.deny with input as {
		"request": {
			"kind": {"kind": "Service"},
			"object": {
				"metadata": {
					"name": "my-service",
					"namespace": "payments",
					"annotations": {"service.beta.kubernetes.io/aws-load-balancer-internal": "true"},
				},
				"spec": {"type": "LoadBalancer"},
			},
		},
	}
	count(msgs) == 0
}

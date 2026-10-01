package kubernetes.admission_test

import data.kubernetes.admission

svc(type, annotations) := {"request": {"kind": {"kind": "Service"}, "object": {
	"metadata": {"name": "svc", "namespace": "payments", "annotations": annotations},
	"spec": {"type": type},
}}}

ingress(spec) := {"request": {"kind": {"kind": "Ingress"}, "object": {
	"metadata": {"name": "ing", "namespace": "payments"},
	"spec": spec,
}}}

test_deny_ingress_without_tls if {
	msgs := admission.deny with input as ingress({"tls": []})
	count(msgs) == 1
	some msg in msgs
	contains(msg, "TLS")
}

test_allow_ingress_with_tls if {
	msgs := admission.deny with input as ingress({"tls": [{"secretName": "tls-cert"}]})
	count(msgs) == 0
}

test_allow_clusterip_service if {
	msgs := admission.deny with input as svc("ClusterIP", {})
	count(msgs) == 0
}

test_warn_nodeport_service if {
	msgs := admission.warn with input as svc("NodePort", {})
	count(msgs) == 1
	some msg in msgs
	contains(msg, "NodePort")
}

test_no_warn_for_clusterip if {
	msgs := admission.warn with input as svc("ClusterIP", {})
	count(msgs) == 0
}

test_nodeport_not_denied if {
	msgs := admission.deny with input as svc("NodePort", {})
	count(msgs) == 0
}

package kubernetes.admission_test

import data.kubernetes.admission

pod(spec) := {"request": {"kind": {"kind": "Pod"}, "object": {"metadata": {"name": "test-pod"}, "spec": spec}}}

ctr(sc) := {"name": "app", "image": "nginx:1.27", "securityContext": sc}

test_deny_privileged_init_container if {
	msgs := admission.deny with input as pod({"containers": [ctr({})], "initContainers": [ctr({"privileged": true})]})
	count(msgs) == 1
	some msg in msgs
	contains(msg, "Privileged init containers")
}

test_deny_run_as_non_root_false if {
	msgs := admission.deny with input as pod({"containers": [ctr({"runAsNonRoot": false})]})
	count(msgs) == 1
}

test_deny_privilege_escalation if {
	msgs := admission.deny with input as pod({"containers": [ctr({"allowPrivilegeEscalation": true})]})
	count(msgs) == 1
}

test_deny_host_pid if {
	msgs := admission.deny with input as pod({"hostPID": true, "containers": [ctr({})]})
	count(msgs) == 1
	some msg in msgs
	contains(msg, "hostPID")
}

test_deny_host_network if {
	msgs := admission.deny with input as pod({"hostNetwork": true, "containers": [ctr({})]})
	count(msgs) == 1
	some msg in msgs
	contains(msg, "hostNetwork")
}

test_allow_hardened_pod_with_init_container if {
	hardened := {"privileged": false, "runAsNonRoot": true, "runAsUser": 1000, "allowPrivilegeEscalation": false}
	msgs := admission.deny with input as pod({"containers": [ctr(hardened)], "initContainers": [ctr(hardened)]})
	count(msgs) == 0
}

test_ignore_non_pod_kinds if {
	msgs := admission.deny with input as {"request": {"kind": {"kind": "Service"}, "object": {"metadata": {"name": "x"}, "spec": {"hostNetwork": true}}}}
	count(msgs) == 0
}

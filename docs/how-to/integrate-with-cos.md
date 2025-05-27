# How to integrate with COS

## prometheus-k8s

Deploy and relate [prometheus-k8s](https://charmhub.io/prometheus-k8s) charm with penpot
charm through the `metrics-endpoint` relation via `prometheus_scrape` interface. Prometheus should
start scraping the metrics exposed at `:9117/metrics` endpoint.

```
juju deploy prometheus-k8s
juju integrate penpot prometheus-k8s
```

## loki-k8s

Deploy and relate [loki-k8s](https://charmhub.io/loki-k8s) charm with penpot charm through
the `logging` relation via `loki_push_api` interface. Pebble inside the 
`penpot` container should be configured to send logs to Loki.


```
juju deploy loki-k8s
juju integrate penpot loki-k8s
```

## grafana-k8s

In order for the Grafana dashboard to function properly, Grafana should be able to connect to
Prometheus and Loki as its datasource. Deploy and relate the `prometheus-k8s` and `loki-k8s`
charms with [grafana-k8s](https://charmhub.io/grafana-k8s) charm through the `grafana-source` integration.

Note that the integration `grafana-source` has to be explicitly stated since `prometheus-k8s` and
`grafana-k8s` share multiple interfaces.

```
juju deploy grafana-k8s
juju integrate prometheus-k8s:grafana-source grafana-k8s:grafana-source
juju integrate loki-k8s:grafana-source grafana-k8s:grafana-source
```

Then, the `penpot` charm can be related with Grafana using the `grafana-dashboard` relation with
`grafana_dashboard` interface.

```
juju integrate penpot grafana-k8s
```

To access the Grafana dashboard for the Penpot charm, run the `get-admin-password` action
to obtain credentials for admin access.

```
juju run grafana-k8s/0 get-admin-password
```

Log into the Grafana dashboard by visiting `http://<grafana-unit-ip>:3000`. Navigate to
`http://<grafana-unit-ip>:3000/dashboards` and access the Penpot dashboard named Penpot Operator
Overview.

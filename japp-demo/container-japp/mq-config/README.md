# MQ 設定說明 — ActiveMQ Artemis（IBM MQ Light 相容）

## Broker Instance 資訊

| 項目 | 值 |
|------|----|
| Broker 安裝目錄 | `/opt/artemis-dist/` |
| Broker Instance 路徑 | `/var/lib/artemis-broker/` |
| 設定主檔 | `/var/lib/artemis-broker/etc/broker.xml` |
| AMQP Port | **5672**（外部可見） |
| Artemis Core Port | 61616（內部，可選） |
| 管理 Web Console | http://localhost:**8161** |
| 管理帳號 | `admin` / `admin123` |

## 佇列定義

| 佇列名稱 | 用途 |
|----------|------|
| `bankingQueue` | Liberty REST layer → 此佇列 → Liberty MDB 消費（主業務佇列） |
| `bankingReplyQueue` | MDB 回寫查詢結果 → Liberty JmsTemplate.sendAndReceive 接收 |

## 自訂 broker.xml

`broker.xml` 已預先定義上述佇列，並啟用 AMQP 1.0 acceptor。

若需自訂設定，可透過 **volume mount 覆蓋**，無需重建 image：

```yaml
# podman-compose.yml 中 container-japp service 的 volumes 加入：
volumes:
  - ./container-japp/mq-config/broker.xml:/var/lib/artemis-broker/etc/broker.xml:ro
```

修改完 `broker.xml` 後，只需重啟容器：
```bash
podman-compose restart container-japp
```

## 常用管理指令

```bash
# 進入容器
podman exec -it container-japp bash

# 查看 broker 狀態
/var/lib/artemis-broker/bin/artemis check node

# 查看佇列狀態
/var/lib/artemis-broker/bin/artemis queue stat --user admin --password admin123

# 查看 AMQP 連線
/var/lib/artemis-broker/bin/artemis check port --host localhost --port 5672
```

## Artemis 日誌位置

容器內：`/logs/artemis/artemis.log`（掛載至 `app_logs` shared volume）

---

## Liberty 連接 Artemis（server.xml 設定）

Liberty 透過 Qpid JMS Client（AMQP 1.0）連接 Artemis：

```xml
<!-- server.xml 中的 JMS Connection Factory -->
<jmsConnectionFactory jndiName="jms/bankingConnectionFactory"
                      connectionManagerRef="mqCM">
  <properties.qpid
    remoteURI="amqp://localhost:5672"
    username="admin"
    password="admin123"/>
</jmsConnectionFactory>
```

詳細設定見 `liberty-config/server.xml`。

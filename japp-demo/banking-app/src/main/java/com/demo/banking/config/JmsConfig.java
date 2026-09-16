// Updated: 2026-09-15 19:56:07 +0800
package com.demo.banking.config;

import org.apache.activemq.artemis.jms.client.ActiveMQConnectionFactory;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.jms.core.JmsTemplate;

import jakarta.jms.ConnectionFactory;
import javax.sql.DataSource;

/**
 * Spring JMS + DataSource 設定。
 *
 * <ul>
 *   <li><b>JMS</b>: ActiveMQConnectionFactory 直連 Artemis（繞過 Liberty JCA）</li>
 *   <li><b>DataSource</b>: {@code PGSimpleDataSource}（PostgreSQL 原生，打包進 WAR）。
 *       每次 {@code getConnection()} 建立新連線，完全繞過 Liberty JNDI 和連線池，
 *       避免 Liberty thread context 下的 DNS 解析慢問題。</li>
 * </ul>
 */
@Configuration
public class JmsConfig {

    @Value("${mq.broker-url:tcp://localhost:61616}")
    private String brokerUrl;

    @Value("${mq.user:admin}")
    private String mqUser;

    @Value("${mq.password:admin123}")
    private String mqPassword;

    @Value("${db.host:banking-db}")
    private String dbHost;

    @Value("${db.port:5432}")
    private String dbPort;

    @Value("${db.name:bankdb}")
    private String dbName;

    @Value("${db.user:db2inst1}")
    private String dbUser;

    @Value("${db.pass:db2admin123}")
    private String dbPass;

    /**
     * ActiveMQ Artemis ConnectionFactory（直連 CORE protocol port 61616）
     */
    @Bean
    public ConnectionFactory connectionFactory() {
        String url = brokerUrl.contains("?") ? brokerUrl : brokerUrl + "?ha=false&reconnectAttempts=0";
        ActiveMQConnectionFactory cf = new ActiveMQConnectionFactory(url);
        cf.setUser(mqUser);
        cf.setPassword(mqPassword);
        cf.setDeserializationWhiteList("*");
        return cf;
    }

    /**
     * JmsTemplate — TRANSFER fire-and-forget 用
     */
    @Bean
    public JmsTemplate jmsTemplate(ConnectionFactory connectionFactory) {
        JmsTemplate tpl = new JmsTemplate(connectionFactory);
        tpl.setReceiveTimeout(5_000L);
        tpl.setPubSubDomain(false);
        return tpl;
    }

    /**
     * DataSource — PostgreSQL PGSimpleDataSource（打包進 WAR，完全繞過 Liberty JNDI）。
     * 簡單直連，無連線池初始化開銷，適合 demo 環境。
     */
    @Bean
    public DataSource dataSource() {
        PGSimpleDataSource ds = new PGSimpleDataSource();
        ds.setServerNames(new String[]{dbHost});
        ds.setPortNumbers(new int[]{Integer.parseInt(dbPort)});
        ds.setDatabaseName(dbName);
        ds.setCurrentSchema("banking");
        ds.setUser(dbUser);
        ds.setPassword(dbPass);
        ds.setConnectTimeout(10);  // 10 秒連線 timeout
        return ds;
    }
}

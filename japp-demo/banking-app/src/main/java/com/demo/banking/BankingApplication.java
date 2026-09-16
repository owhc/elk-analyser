// Updated: 2026-09-03 15:06:08 +0800
package com.demo.banking;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.web.servlet.support.SpringBootServletInitializer;

/**
 * Spring Boot 3.x WAR entry point — deployed on WAS Liberty.
 *
 * <p>Liberty acts as the servlet container; embedded Tomcat is excluded.
 * {@link SpringBootServletInitializer} wires the Spring ApplicationContext
 * into the Liberty servlet context on startup.</p>
 *
 * <p>{@code ErrorPageFilter} is disabled: in Liberty the WebContainer
 * intercepts its error-page forward, causing POST responses to hang
 * (header sent but body never flushed). Disabling the filter restores
 * normal response behaviour.</p>
 */
@SpringBootApplication(
    scanBasePackages = {
        "com.demo.banking.controller",
        "com.demo.banking.service",
        "com.demo.banking.config",
        "com.demo.banking.model",
        "com.demo.banking.mdb"
    }
)
public class BankingApplication extends SpringBootServletInitializer {

    /**
     * Called by the servlet container (Liberty) on WAR deployment.
     * Disables ErrorPageFilter by excluding the ErrorMvcAutoConfiguration —
     * this prevents POST response body hang caused by Liberty WebContainer
     * intercepting Spring's /error forward.
     */
    @Override
    protected SpringApplicationBuilder configure(SpringApplicationBuilder application) {
        return application
            .sources(BankingApplication.class)
            .properties("spring.autoconfigure.exclude=org.springframework.boot.autoconfigure.web.servlet.error.ErrorMvcAutoConfiguration");
    }

    /**
     * Standard main method — used only for standalone testing outside Liberty.
     */
    public static void main(String[] args) {
        SpringApplication.run(BankingApplication.class, args);
    }
}

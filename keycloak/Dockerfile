FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /src
COPY pom.xml .
COPY src src
RUN mvn -q -DskipTests package

FROM quay.io/keycloak/keycloak:26.8.0
COPY --from=build /src/target/workspace-login-1.0.0.jar /opt/keycloak/providers/
RUN /opt/keycloak/bin/kc.sh build

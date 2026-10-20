package com.reconq.keycloak;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import org.jboss.logging.Logger;
import org.json.JSONObject;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.AuthenticationFlowError;
import org.keycloak.authentication.Authenticator;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.UserModel;

final class WorkspaceTokenAuthenticator implements Authenticator {
  private static final Logger LOG = Logger.getLogger(WorkspaceTokenAuthenticator.class);
  private final HttpClient client = HttpClient.newBuilder()
      .version(HttpClient.Version.HTTP_1_1)
      .connectTimeout(Duration.ofSeconds(5))
      .build();

  public void authenticate(AuthenticationFlowContext context) {
    String token = context.getHttpRequest().getUri().getQueryParameters().getFirst("workspace_token");
    if (token == null || token.isBlank()) {
      LOG.debugf("Workspace link authentication skipped: workspace_token is missing");
      context.attempted(); return;
    }
    try {
      String base = System.getenv().getOrDefault("ONBOARDING_PUBLIC_URL", "http://127.0.0.1:8001");
      JSONObject body = new JSONObject().put("token", token);
      HttpRequest request = HttpRequest.newBuilder(URI.create(base + "/api/public/workspace-access-token/consume"))
          .timeout(Duration.ofSeconds(10)).header("Content-Type", "application/json")
          .header("X-Workspace-Token", token)
          .POST(HttpRequest.BodyPublishers.ofString(body.toString())).build();
      HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() != 200) {
        LOG.warnf("Workspace link rejected by onboarding: HTTP %d, body=%s", response.statusCode(), response.body());
        context.failure(AuthenticationFlowError.INVALID_CREDENTIALS); return;
      }
      JSONObject access = new JSONObject(response.body());
      UserModel user = context.getSession().users().getUserByEmail(context.getRealm(), access.getString("email"));
      boolean userMatches = user != null && user.isEnabled() && user.getId().equals(access.getString("user_id"));
      if (!userMatches) {
        LOG.warnf("Workspace link user mismatch: email=%s, userFound=%s, enabled=%s, idMatches=%s",
            access.optString("email"), user != null, user != null && user.isEnabled(),
            user != null && user.getId().equals(access.optString("user_id")));
        context.failure(AuthenticationFlowError.UNKNOWN_USER); return;
      }
      context.setUser(user);
      context.success();
    } catch (Exception error) {
      LOG.warn("Workspace link authentication failed while contacting onboarding", error);
      context.failure(AuthenticationFlowError.GENERIC_AUTHENTICATION_ERROR);
    }
  }
  public void action(AuthenticationFlowContext context) { authenticate(context); }
  public boolean requiresUser() { return false; }
  public boolean configuredFor(KeycloakSession session, org.keycloak.models.RealmModel realm, UserModel user) { return true; }
  public void setRequiredActions(KeycloakSession session, org.keycloak.models.RealmModel realm, UserModel user) {}
  public void close() {}
}

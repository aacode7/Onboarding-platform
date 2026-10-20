package com.reconq.keycloak;

import java.util.List;
import org.keycloak.Config;
import org.keycloak.authentication.Authenticator;
import org.keycloak.authentication.AuthenticatorFactory;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.models.AuthenticationExecutionModel;
import org.keycloak.provider.ProviderConfigProperty;

public final class WorkspaceTokenAuthenticatorFactory implements AuthenticatorFactory {
  public static final String ID = "workspace-token";
  private static final WorkspaceTokenAuthenticator SINGLETON = new WorkspaceTokenAuthenticator();
  public Authenticator create(KeycloakSession session) { return SINGLETON; }
  public String getId() { return ID; }
  public String getReferenceCategory() { return ID; }
  public boolean isConfigurable() { return false; }
  public boolean isUserSetupAllowed() { return false; }
  public AuthenticationExecutionModel.Requirement[] getRequirementChoices() {
    return new AuthenticationExecutionModel.Requirement[] {
      AuthenticationExecutionModel.Requirement.REQUIRED,
      AuthenticationExecutionModel.Requirement.ALTERNATIVE,
      AuthenticationExecutionModel.Requirement.DISABLED,
    };
  }
  public List<ProviderConfigProperty> getConfigProperties() { return List.of(); }
  public String getDisplayType() { return "ReconQ workspace email link"; }
  public String getHelpText() { return "Consumes a single-use onboarding workspace token."; }
  public void init(Config.Scope config) {}
  public void postInit(KeycloakSessionFactory factory) {}
  public void close() {}
}

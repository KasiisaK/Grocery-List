import Keycloak from 'keycloak-js'

export const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL ?? 'http://localhost:8081',
  realm: import.meta.env.VITE_KEYCLOAK_REALM ?? 'grocery',
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? 'grocery-frontend',
})

// Redirects to the Keycloak login page if nobody is signed in.
// Resolves once the user is signed in again and back in the app.
export function initAuth() {
  return keycloak.init({
    onLoad: 'login-required',
    pkceMethod: 'S256',
    checkLoginIframe: false,
  })
}

export function logout() {
  keycloak.logout({ redirectUri: window.location.origin + '/' })
}

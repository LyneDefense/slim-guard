import * as SecureStore from 'expo-secure-store';

const REFRESH_TOKEN_KEY = 'slimguard.refresh-token.v1';
const INSTALLATION_ID_KEY = 'slimguard.installation-id.v1';

export async function readRefreshToken(): Promise<string | null> {
  return SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
}

export async function saveRefreshToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, token, {
    keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  });
}

export async function clearRefreshToken(): Promise<void> {
  await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
}

export async function readInstallationId(): Promise<string | null> {
  return SecureStore.getItemAsync(INSTALLATION_ID_KEY);
}

export async function saveInstallationId(value: string): Promise<void> {
  await SecureStore.setItemAsync(INSTALLATION_ID_KEY, value, {
    keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  });
}

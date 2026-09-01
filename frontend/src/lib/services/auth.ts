import { apiGet, apiPost } from '../api';

export type AuthUser = {
  id: number;
  username: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_at: string;
};

export const login = (username: string, password: string) =>
  apiPost<LoginResponse>('/auth/login', { username, password });

export const fetchMe = () => apiGet<AuthUser>('/auth/me');

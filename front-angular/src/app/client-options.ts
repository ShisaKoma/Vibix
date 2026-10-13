export interface SettingOption { value: string; label: string; }
export interface ClientOptions { templates: SettingOption[]; colorSchemes: SettingOption[]; }
export const DEFAULT_CLIENT_OPTIONS: ClientOptions = {
  templates: [{value: 'bloc', label: 'Bloc'}, {value: 'shape', label: 'Shape'}, {value: 'custom', label: 'Custom'}],
  colorSchemes: [{value: 'light', label: 'Light'}, {value: 'dark', label: 'Dark'}]
};

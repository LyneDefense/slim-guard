import appConfig from '../../../app.json';

// Display identity only. App identifiers, storage keys and server protocols stay stable.
export const brand = {
  name: appConfig.expo.name,
  tagline: '帮你记着，也帮你盯着。',
  colors: {
    background: '#F3BE96',
    ink: '#503F3C',
    inkMuted: '#664D43',
  },
} as const;

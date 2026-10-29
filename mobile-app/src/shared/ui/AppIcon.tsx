import React from 'react';
import Svg, { Circle, Path } from 'react-native-svg';

export type AppIconName = 'scale' | 'meal' | 'exercise' | 'chat' | 'sun' | 'progress' | 'profile' | 'clock' | 'steps' | 'expand';

// Small, consistent outline icons; SVGs stay crisp at native text/thumbnail sizes.
export function AppIcon({ name, size = 24, color, accent }: {
  name: AppIconName; size?: number; color: string; accent?: string;
}) {
  return <Svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke={color} strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" accessible={false}>
    {name === 'scale' && <>
      <Path d="M10 8V6.5C10 4.5 12.5 3 16 3s6 1.5 6 3.5V8h1.2a3 3 0 0 1 3 2.6l1.5 15a3 3 0 0 1-3 3.4H7.3a3 3 0 0 1-3-3.4l1.5-15A3 3 0 0 1 8.8 8H10Z" />
      <Path d="M12 11c1.5 1.5 6.5 1.5 8 0" />
    </>}
    {name === 'meal' && <>
      <Path d="M5 3v8c0 3 2 4 4 4s4-1 4-4V3M9 3v26M22 18v11M22 3c-3 0-4 4-4 8s1 7 4 7 4-3 4-7-1-8-4-8Z" />
    </>}
    {name === 'exercise' && <>
      <Circle cx="19" cy="5" r="3" fill={color} stroke="none" />
      <Path d="m8 12 4-4 6 3 3 5 5 1M17 11l-4 8 6 4-1 7M13 19l-3 7H4" strokeWidth={2.8} />
    </>}
    {name === 'chat' && <>
      <Path d="M5.5 23A12 12 0 1 1 11 27l-7 2 1.5-6Z" />
      {[10, 16, 22].map(cx => <Circle key={cx} cx={cx} cy="15" r="1.6" fill={color} stroke="none" />)}
    </>}
    {name === 'sun' && <>
      <Circle cx="16" cy="16" r="6" fill={accent || 'none'} stroke={accent ? 'none' : color} />
      <Path d="M16 2v4m0 20v4M2 16h4m20 0h4M6 6l3 3m14 14 3 3M6 26l3-3M23 9l3-3" stroke={accent || color} strokeWidth={2.2} />
    </>}
    {name === 'progress' && <>
      <Path d="m4 24 8-12 9 7 7-13" />
      {[[4,24],[12,12],[21,19],[28,6]].map(([cx,cy]) => <Circle key={cx} cx={cx} cy={cy} r="2.1" fill="white" />)}
    </>}
    {name === 'profile' && <>
      <Circle cx="16" cy="9" r="6" /><Path d="M4 29c0-7 5-11 12-11s12 4 12 11H4Z" />
    </>}
    {name === 'clock' && <><Circle cx="16" cy="16" r="12" /><Path d="M16 8v9l6 3" /></>}
    {name === 'steps' && <>
      <Path d="M9 3C5 3 3 11 5 16c1 2 5 2 6-1 2-5 2-12-2-12Zm14 6c-4-1-6 6-6 11 0 3 4 4 6 2 4-4 5-12 0-13ZM6 21l4 1-1 5-4-1 1-5Zm11 5 4 2-2 3-4-2 2-3Z" fill={color} stroke="none" />
    </>}
    {name === 'expand' && <Path d="M19 4h9v9M28 4 18 14M13 28H4v-9M4 28l10-10" />}
  </Svg>;
}

// The approved apricot × indigo visual language, shared by home, chat, history and navigation.
// Other screens keep their existing theme until their own layouts are redesigned.
export const appearance = {
  background: '#FBF9F6',
  surface: '#FFFFFF',
  ink: '#14204C',
  muted: '#73798B',
  line: '#E9E9ED',
  indigo: '#4B588E',
  lavender: '#EEEFFC',
  apricot: '#FBC098',
  peach: '#FFE5D5',
  orange: '#CE4E09',
  orangeInk: '#763B1D',
  gold: '#FFF0C2',
  blue: '#E0EFF8',
  shadow: {
    shadowColor: '#5C5262', shadowOffset: { width: 0, height: 5 },
    shadowOpacity: 0.035, shadowRadius: 16, elevation: 1,
  },
} as const;

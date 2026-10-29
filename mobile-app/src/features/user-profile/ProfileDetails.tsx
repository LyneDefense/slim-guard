import React from 'react';

import { CoachMemoryScreen } from '../coach-memory/CoachMemoryScreen';
import { AccountScreen } from './AccountScreen';
import { ReminderScreen } from './ReminderScreen';
import { WeComScreen } from './WeComScreen';

export type ProfilePage = 'memory' | 'reminders' | 'wecom' | 'account';

// Public detail-page entry point; App owns routing, each page owns its form state.
export function ProfileDetails({ page, onClose, openCoach }: { page: ProfilePage; onClose: () => void; openCoach: (draft?: string) => void }) {
  switch (page) {
    case 'memory': return <CoachMemoryScreen onClose={onClose} openCoach={openCoach} />;
    case 'reminders': return <ReminderScreen onClose={onClose} />;
    case 'wecom': return <WeComScreen onClose={onClose} />;
    case 'account': return <AccountScreen onClose={onClose} />;
  }
}

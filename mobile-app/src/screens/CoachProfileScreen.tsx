import DateTimePicker from '@expo/ui/community/datetime-picker';
import { Ionicons } from '@expo/vector-icons';
import React, { useMemo, useState } from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button, Card } from '../components/ui';
import { useApp } from '../context/AppContext';
import { colors, radius, spacing } from '../theme';
import type {
  CoachAgeBand,
  CoachExerciseFrequency,
  CoachGoalType,
  CoachProfileInput,
} from '../types';

const AGE_OPTIONS: Array<{ value: CoachAgeBand; label: string }> = [
  { value: '0_9', label: '0～9 岁' },
  { value: '10_17', label: '10～17 岁' },
  { value: '18_29', label: '18～29 岁' },
  { value: '30_39', label: '30～39 岁' },
  { value: '40_49', label: '40～49 岁' },
  { value: '50_59', label: '50～59 岁' },
  { value: '60_69', label: '60～69 岁' },
  { value: '70_79', label: '70～79 岁' },
  { value: '80_plus', label: '80 岁及以上' },
];

const GOAL_OPTIONS: Array<{ value: CoachGoalType; label: string }> = [
  { value: 'lose_weight', label: '减重' },
  { value: 'maintain_weight', label: '维持体重' },
  { value: 'improve_habits', label: '改善习惯' },
];

const EXERCISE_OPTIONS: Array<{ value: CoachExerciseFrequency; label: string }> = [
  { value: 'rarely', label: '基本不运动' },
  { value: 'weekly_1_2', label: '每周 1～2 次' },
  { value: 'weekly_3_4', label: '每周 3～4 次' },
  { value: 'weekly_5_plus', label: '每周 5 次及以上' },
];

type Props = {
  intent: 'coach' | 'edit';
  onClose: () => void;
  onReady: () => void;
};

export function CoachProfileScreen({ intent, onClose, onReady }: Props) {
  const { data, saveCoachProfile } = useApp();
  const existing = data?.coach_profile.profile ?? null;
  const [ageBand, setAgeBand] = useState<CoachAgeBand | null>(existing?.age_band ?? null);
  const [height, setHeight] = useState(numberText(existing?.height_cm));
  const [currentWeight, setCurrentWeight] = useState(numberText(existing?.current_weight_kg));
  const [weightDate, setWeightDate] = useState(existing?.weight_measured_on ?? todayText());
  const [goalType, setGoalType] = useState<CoachGoalType | null>(existing?.goal_type ?? null);
  const [targetWeight, setTargetWeight] = useState(numberText(existing?.target_weight_kg));
  const [targetDate, setTargetDate] = useState(existing?.target_date ?? '');
  const [currentBodyFat, setCurrentBodyFat] = useState(numberText(existing?.current_body_fat_percent));
  const [targetBodyFat, setTargetBodyFat] = useState(numberText(existing?.target_body_fat_percent));
  const [exercise, setExercise] = useState<CoachExerciseFrequency | null>(existing?.exercise_frequency ?? null);
  const [saving, setSaving] = useState(false);

  const validation = useMemo(() => validateForm({
    ageBand,
    height,
    currentWeight,
    weightDate,
    goalType,
    targetWeight,
    targetDate,
    currentBodyFat,
    targetBodyFat,
    exercise,
  }), [ageBand, height, currentWeight, weightDate, goalType, targetWeight, targetDate, currentBodyFat, targetBodyFat, exercise]);

  async function save() {
    if (!validation.payload || saving) return;
    setSaving(true);
    try {
      const result = await saveCoachProfile(validation.payload);
      if (result.coach_enabled && intent === 'coach') onReady();
      else onClose();
    } catch (caught) {
      Alert.alert('没有保存成功', caught instanceof Error ? caught.message : '请稍后重试');
    } finally {
      setSaving(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.header}>
          <Pressable accessibilityLabel="返回" onPress={onClose} style={styles.backButton}>
            <Ionicons name="chevron-back" size={22} color={colors.ink} />
          </Pressable>
          <Text style={styles.headerTitle}>健康档案</Text>
          <View style={styles.headerSpacer} />
        </View>
        <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
          <Text style={styles.eyebrow}>BEFORE COACHING</Text>
          <Text style={styles.title}>先把必要资料{`\n`}填写完整。</Text>
          <Text style={styles.intro}>教练会依据这些数据理解你的当前状态和目标。必填项保存后才能使用教练。</Text>

          <SectionLabel title="基本情况" required />
          <Card>
            <ChoiceField label="年龄段" options={AGE_OPTIONS} value={ageBand} onChange={(value) => {
              setAgeBand(value);
            }} />
            <FieldDivider />
            <NumberField label="身高" unit="cm" value={height} onChange={setHeight} placeholder="例如 168.0" />
            <FieldDivider />
            <NumberField label="当前体重" unit="kg" value={currentWeight} onChange={setCurrentWeight} placeholder="例如 72.5" />
            <FieldDivider />
            <DateField label="体重测量日期" value={weightDate} onChange={setWeightDate} maximumDate={startOfToday()} />
          </Card>

          <SectionLabel title="你的目标" required />
          <Card>
            <ChoiceField label="使用目标" options={GOAL_OPTIONS} value={goalType} onChange={setGoalType} />
            <FieldDivider />
            <NumberField label="目标体重" unit="kg" value={targetWeight} onChange={setTargetWeight} placeholder="例如 65.0" />
            <FieldDivider />
            <DateField label="目标日期" value={targetDate} onChange={setTargetDate} minimumDate={tomorrow()} />
          </Card>

          <SectionLabel title="补充信息" optional />
          <Card>
            <NumberField label="当前体脂率" unit="%" value={currentBodyFat} onChange={setCurrentBodyFat} placeholder="不填也可以" />
            <FieldDivider />
            <NumberField label="目标体脂率" unit="%" value={targetBodyFat} onChange={setTargetBodyFat} placeholder="不填也可以" />
            <FieldDivider />
            <ChoiceField label="运动习惯" options={EXERCISE_OPTIONS} value={exercise} onChange={setExercise} allowClear />
          </Card>

          {validation.error ? <Text style={styles.validation}>{validation.error}</Text> : null}
          <Button
            title={intent === 'coach' ? '保存并进入教练' : '保存资料'}
            loading={saving}
            disabled={!validation.payload}
            onPress={() => void save()}
            style={styles.saveButton}
          />
          <Text style={styles.footnote}>目标数据来自你的手动填写，不代表医学诊断或系统对目标安全性的保证。</Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function SectionLabel({ title, required, optional }: { title: string; required?: boolean; optional?: boolean }) {
  return (
    <View style={styles.sectionLabel}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <Text style={required ? styles.required : styles.optional}>{required ? '必填' : optional ? '选填' : ''}</Text>
    </View>
  );
}

function ChoiceField<T extends string>({
  label,
  options,
  value,
  onChange,
  allowClear = false,
}: {
  label: string;
  options: Array<{ value: T; label: string }>;
  value: T | null;
  onChange: (value: T | null) => void;
  allowClear?: boolean;
}) {
  return (
    <View>
      <Text style={styles.fieldLabel}>{label}</Text>
      <View style={styles.choiceGrid}>
        {options.map((option) => {
          const selected = value === option.value;
          return (
            <Pressable
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              key={option.value}
              onPress={() => onChange(allowClear && selected ? null : option.value)}
              style={[styles.choice, selected && styles.choiceSelected]}
            >
              <Text style={[styles.choiceText, selected && styles.choiceTextSelected]}>{option.label}</Text>
            </Pressable>
          );
        })}
      </View>
      {allowClear ? <Text style={styles.fieldHint}>再次点击已选项可清除</Text> : null}
    </View>
  );
}

function NumberField({ label, unit, value, onChange, placeholder }: {
  label: string;
  unit: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <View>
      <Text style={styles.fieldLabel}>{label}</Text>
      <View style={styles.numberField}>
        <TextInput
          accessibilityLabel={label}
          value={value}
          onChangeText={(next) => onChange(sanitizeNumber(next))}
          keyboardType="decimal-pad"
          maxLength={6}
          placeholder={placeholder}
          placeholderTextColor="#949B96"
          style={styles.numberInput}
        />
        <Text style={styles.unit}>{unit}</Text>
      </View>
    </View>
  );
}

function DateField({ label, value, onChange, minimumDate, maximumDate }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  minimumDate?: Date;
  maximumDate?: Date;
}) {
  const [open, setOpen] = useState(false);
  const fallback = minimumDate ?? maximumDate ?? startOfToday();
  return (
    <View>
      <Text style={styles.fieldLabel}>{label}</Text>
      <Pressable accessibilityRole="button" onPress={() => setOpen(true)} style={styles.dateButton}>
        <Text style={[styles.dateText, !value && styles.datePlaceholder]}>{value ? displayDate(value) : '请选择日期'}</Text>
        <Ionicons name="calendar-outline" size={19} color={colors.primary} />
      </Pressable>
      {open ? (
        <View style={styles.datePickerWrap}>
          <DateTimePicker
            value={value ? parseDate(value) : fallback}
            mode="date"
            display={Platform.OS === 'ios' ? 'compact' : 'default'}
            presentation="dialog"
            locale="zh_CN"
            minimumDate={minimumDate}
            maximumDate={maximumDate}
            positiveButton={{ label: '确定' }}
            negativeButton={{ label: '取消' }}
            onDismiss={() => setOpen(false)}
            onValueChange={(_event, selected) => {
              onChange(dateText(selected));
              setOpen(false);
            }}
          />
        </View>
      ) : null}
    </View>
  );
}

function FieldDivider() {
  return <View style={styles.divider} />;
}

type FormState = {
  ageBand: CoachAgeBand | null;
  height: string;
  currentWeight: string;
  weightDate: string;
  goalType: CoachGoalType | null;
  targetWeight: string;
  targetDate: string;
  currentBodyFat: string;
  targetBodyFat: string;
  exercise: CoachExerciseFrequency | null;
};

function validateForm(form: FormState): { payload: CoachProfileInput | null; error: string | null } {
  if (!form.ageBand || !form.goalType || !form.weightDate || !form.targetDate) {
    return { payload: null, error: '请填写全部必填项' };
  }
  const height = parseNumber(form.height);
  const currentWeight = parseNumber(form.currentWeight);
  const targetWeight = parseNumber(form.targetWeight);
  if (height === null || height < 50 || height > 250) return { payload: null, error: '身高请输入 50～250 cm，最多一位小数' };
  if (currentWeight === null || currentWeight < 10 || currentWeight > 500) return { payload: null, error: '当前体重请输入 10～500 kg，最多一位小数' };
  if (targetWeight === null || targetWeight < 10 || targetWeight > 500) return { payload: null, error: '目标体重请输入 10～500 kg，最多一位小数' };
  if (parseDate(form.weightDate) > startOfToday()) return { payload: null, error: '体重测量日期不能晚于今天' };
  if (parseDate(form.targetDate) <= startOfToday() || parseDate(form.targetDate) <= parseDate(form.weightDate)) return { payload: null, error: '目标日期必须晚于今天和体重测量日期' };
  if (form.goalType === 'lose_weight' && targetWeight >= currentWeight) return { payload: null, error: '选择减重时，目标体重需要低于当前体重' };
  const currentBodyFat = optionalNumber(form.currentBodyFat);
  const targetBodyFat = optionalNumber(form.targetBodyFat);
  if (currentBodyFat === 'invalid') return { payload: null, error: '当前体脂率请输入 1～75%，最多一位小数' };
  if (targetBodyFat === 'invalid') return { payload: null, error: '目标体脂率请输入 1～75%，最多一位小数' };
  return {
    error: null,
    payload: {
      age_band: form.ageBand,
      height_cm: height,
      current_weight_kg: currentWeight,
      weight_measured_on: form.weightDate,
      goal_type: form.goalType,
      target_weight_kg: targetWeight,
      target_date: form.targetDate,
      current_body_fat_percent: currentBodyFat,
      target_body_fat_percent: targetBodyFat,
      exercise_frequency: form.exercise,
    },
  };
}

function sanitizeNumber(value: string): string {
  const normalized = value.replace(',', '.').replace(/[^0-9.]/g, '');
  const [whole = '', ...fractions] = normalized.split('.');
  if (fractions.length === 0) return whole;
  return `${whole}.${fractions.join('').slice(0, 1)}`;
}

function parseNumber(value: string): number | null {
  if (!/^\d+(?:\.\d)?$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function optionalNumber(value: string): number | null | 'invalid' {
  if (!value) return null;
  const parsed = parseNumber(value);
  return parsed !== null && parsed >= 1 && parsed <= 75 ? parsed : 'invalid';
}

function numberText(value: number | null | undefined): string {
  return typeof value === 'number' ? String(value) : '';
}

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
}

function tomorrow(): Date {
  const result = startOfToday();
  result.setDate(result.getDate() + 1);
  return result;
}

function todayText(): string {
  return dateText(startOfToday());
}

function parseDate(value: string): Date {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, month - 1, day, 12);
}

function dateText(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function displayDate(value: string): string {
  const date = parseDate(value);
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  safe: { flex: 1, backgroundColor: colors.background },
  header: { height: 58, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line, backgroundColor: colors.surface },
  backButton: { width: 42, height: 42, alignItems: 'center', justifyContent: 'center', borderRadius: 14 },
  headerTitle: { color: colors.ink, fontSize: 16, fontWeight: '800' },
  headerSpacer: { width: 42 },
  page: { padding: spacing.lg, paddingTop: spacing.xl, paddingBottom: 48 },
  eyebrow: { color: colors.primary, fontSize: 11, fontWeight: '800', letterSpacing: 1.7 },
  title: { color: colors.ink, fontSize: 30, lineHeight: 38, fontWeight: '800', letterSpacing: -0.9, marginTop: spacing.sm },
  intro: { color: colors.inkMuted, fontSize: 13, lineHeight: 20, marginTop: spacing.md },
  sectionLabel: { marginTop: spacing.xl, marginBottom: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  sectionTitle: { color: colors.ink, fontSize: 19, fontWeight: '800' },
  required: { color: colors.primary, fontSize: 11, fontWeight: '800' },
  optional: { color: colors.inkMuted, fontSize: 11, fontWeight: '700' },
  fieldLabel: { color: colors.inkMuted, fontSize: 12, fontWeight: '700', marginBottom: spacing.sm },
  choiceGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  choice: { minHeight: 40, paddingHorizontal: spacing.md, alignItems: 'center', justifyContent: 'center', borderRadius: 13, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surfaceMuted },
  choiceSelected: { borderColor: colors.primary, backgroundColor: colors.primarySoft },
  choiceText: { color: colors.inkMuted, fontSize: 13, fontWeight: '600' },
  choiceTextSelected: { color: colors.primaryDark, fontWeight: '800' },
  fieldHint: { color: colors.inkMuted, fontSize: 10, marginTop: spacing.sm },
  divider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.line, marginVertical: spacing.lg },
  numberField: { minHeight: 49, flexDirection: 'row', alignItems: 'center', borderRadius: radius.md, backgroundColor: colors.surfaceMuted },
  numberInput: { flex: 1, minHeight: 49, paddingHorizontal: spacing.md, color: colors.ink, fontSize: 17, fontWeight: '700' },
  unit: { minWidth: 48, paddingRight: spacing.md, color: colors.inkMuted, fontSize: 13, textAlign: 'right' },
  dateButton: { minHeight: 49, paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderRadius: radius.md, backgroundColor: colors.surfaceMuted },
  dateText: { color: colors.ink, fontSize: 15, fontWeight: '700' },
  datePlaceholder: { color: '#949B96', fontWeight: '500' },
  datePickerWrap: { marginTop: spacing.sm, alignItems: 'flex-start' },
  validation: { color: colors.danger, fontSize: 12, lineHeight: 18, marginTop: spacing.lg, textAlign: 'center' },
  saveButton: { marginTop: spacing.lg },
  footnote: { color: colors.inkMuted, fontSize: 10, lineHeight: 16, textAlign: 'center', marginTop: spacing.md, paddingHorizontal: spacing.lg },
});

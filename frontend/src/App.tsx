import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedShell, LoginPage } from "./app/AuthPages";
import { Workspace } from "./app/Workspace";
import {
  UserDirectoryPage,
  UserLayout,
  MemoriesPage,
  RecordsPage,
  RoutinesPage,
} from "./features/users/UserPages";
import { UsagePage, UsageDetailPage } from "./features/usage/UsagePage";
import { StyleProfilePage } from "./components/style/StyleProfilePage";
import { NutritionKnowledgePage } from "./components/nutrition/NutritionKnowledgePage";
import { CallListPage } from "./features/traces/CallListPage";
import { CallDetailPage } from "./features/traces/CallDetailPage";

export function App() {
  return (
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route element={<ProtectedShell />}>
        <Route index element={<Navigate to="/users" replace />} />
        <Route path="users" element={<CallListPage />} />
        <Route
          path="users/directory"
          element={
            <Workspace title="用户管理">
              <UserDirectoryPage />
            </Workspace>
          }
        />
        <Route
          path="usage"
          element={
            <Workspace title="模型用量 / 按用户">
              <UsagePage />
            </Workspace>
          }
        />
        <Route
          path="usage/jobs"
          element={
            <Workspace title="模型用量 / 系统任务">
              <UsagePage jobs />
            </Workspace>
          }
        />
        <Route
          path="usage/jobs/:jobId"
          element={
            <Workspace title="模型用量 / 任务详情">
              <UsageDetailPage />
            </Workspace>
          }
        />
        <Route
          path="styles"
          element={
            <Workspace title="表达风格">
              <StyleProfilePage />
            </Workspace>
          }
        />
        <Route
          path="styles/:styleId/:tab"
          element={
            <Workspace title="表达风格 / 版本与素材">
              <StyleProfilePage />
            </Workspace>
          }
        />
        <Route
          path="nutrition-knowledge"
          element={
            <Workspace title="营养知识库">
              <NutritionKnowledgePage />
            </Workspace>
          }
        />
        <Route path="users/:userId" element={<UserLayout />}>
          <Route index element={<CallListPage />} />
          <Route path="traces/:traceId" element={<CallDetailPage />} />
          <Route path="memories" element={<MemoriesPage />} />
          <Route path="records" element={<RecordsPage />} />
          <Route path="routines" element={<RoutinesPage />} />
          <Route path="usage" element={<UsagePage />} />
          <Route path="usage/:requestId" element={<UsageDetailPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/users" replace />} />
      </Route>
    </Routes>
  );
}

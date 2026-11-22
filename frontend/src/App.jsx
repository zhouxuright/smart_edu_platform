import React, { lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom';
import LandingPage from './pages/LandingPage';
import { resolveAuthReturnPath } from './services/mentorDraft';
import PageViewTracker from './components/PageViewTracker';
const Login = lazy(() => import('./pages/Login'));
const Register = lazy(() => import('./pages/Register'));
const MainLayout = lazy(() => import('./components/layout'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const MentorAgent = lazy(() => import('./pages/MentorAgent'));
const KnowledgeGraph = lazy(() => import('./pages/KnowledgeGraph'));
const Assessment = lazy(() => import('./pages/Assessment'));
const Collaborative = lazy(() => import('./pages/Collaborative'));
const ParentPortal = lazy(() => import('./pages/ParentPortal'));
const EnterpriseRAG = lazy(() => import('./pages/EnterpriseRAG'));
const MistakeBook = lazy(() => import('./pages/MistakeBook'));
const KnowledgeExtraction = lazy(() => import('./pages/KnowledgeExtraction'));
const InternshipPlan = lazy(() => import('./pages/InternshipPlan'));
const Applications = lazy(() => import('./pages/Applications'));
const ResumeStudio = lazy(() => import('./pages/ResumeStudio'));

const PrivateRoute = () => {
  const { pathname, search } = useLocation();
  if (localStorage.getItem('token')) return <Outlet />;
  return <Navigate to="/login" replace state={{ returnTo: resolveAuthReturnPath(pathname + search) }} />;
};

function MentorLayout() {
  const { search } = useLocation();
  return new URLSearchParams(search).get('focus') === 'career' ? <Outlet /> : <MainLayout />;
}

function App() {
  return (
    <Router>
      <PageViewTracker />
      <Suspense fallback={<p role="status" style={{ margin: 24 }}>页面加载中…</p>}>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route element={<PrivateRoute />}>
            <Route element={<MentorLayout />}>
              <Route path="mentor" element={<MentorAgent />} />
            </Route>
            <Route path="plan/internship" element={<InternshipPlan />} />
            <Route element={<MainLayout />}>
              <Route path="dashboard" element={<Dashboard />} />
              <Route path="knowledge" element={<KnowledgeGraph />} />
              <Route path="rag" element={<EnterpriseRAG />} />
              <Route path="applications" element={<Applications />} />
              <Route path="resume" element={<ResumeStudio />} />
              <Route path="assessment" element={<Assessment />} />
              <Route path="collaborative" element={<Collaborative />} />
              <Route path="parent" element={<ParentPortal />} />
              <Route path="mistake-book" element={<MistakeBook />} />
              <Route path="knowledge-extraction" element={<KnowledgeExtraction />} />
            </Route>
          </Route>
        </Routes>
      </Suspense>
    </Router>
  );
}

export default App;

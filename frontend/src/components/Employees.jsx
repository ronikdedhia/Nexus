import { useState, useEffect, useCallback } from "react";
import { api } from "../api";

export default function Employees() {
  const [employees, setEmployees] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [query, setQuery] = useState("");
  const [department, setDepartment] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (query.trim()) params.q = query.trim();
      if (department) params.department = department;
      const data = await api.listEmployees(params);
      setEmployees(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [query, department]);

  useEffect(() => {
    api.listDepartments().then(setDepartments).catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  return (
    <div className="panel">
      <div className="panel-controls">
        <input
          placeholder="Search name, email, role…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={department} onChange={(e) => setDepartment(e.target.value)}>
          <option value="">All departments</option>
          {departments.map((d) => (
            <option key={d.department} value={d.department}>
              {d.department} ({d.employee_count})
            </option>
          ))}
        </select>
      </div>

      {error && <div className="chat-error">{error}</div>}

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Department</th>
              <th>Role</th>
              <th>Manager</th>
              <th>Leave</th>
              <th>Email</th>
            </tr>
          </thead>
          <tbody>
            {employees.map((e) => (
              <tr key={e.id}>
                <td>{e.id}</td>
                <td>{e.name}</td>
                <td>{e.department}</td>
                <td>{e.role}</td>
                <td>{e.manager}</td>
                <td>{e.leave_balance}</td>
                <td>{e.email}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && employees.length === 0 && <p className="chat-empty">No employees found.</p>}
      </div>
    </div>
  );
}

import { useState, useEffect } from "react";
import { api } from "../api";

export default function Employees() {
  const [employees, setEmployees] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.listDepartments().then(setDepartments).catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    // Debounce typing so each keystroke doesn't fire a request.
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        setEmployees(await api.listEmployees({ q: search.trim(), department, limit: 200 }));
        setError(null);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [search, department]);

  return (
    <div className="panel">
      <div className="panel-controls">
        <input placeholder="Search name, email or role…" value={search} onChange={(e) => setSearch(e.target.value)} />
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
              <th>Role</th>
              <th>Department</th>
              <th>Manager</th>
              <th>Email</th>
            </tr>
          </thead>
          <tbody>
            {employees.map((e) => (
              <tr key={e.id}>
                <td>{e.id}</td>
                <td>{e.name}</td>
                <td>{e.role}</td>
                <td>{e.department}</td>
                <td>{e.manager}</td>
                <td>{e.email}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && employees.length === 0 && <p className="chat-empty table-empty">No employees found.</p>}
      </div>
    </div>
  );
}

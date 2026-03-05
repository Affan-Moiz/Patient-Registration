const express = require('express');
const crypto = require('crypto');
const cors = require('cors');
const db = require('./database');
const { patientValidationRules, patientUpdateRules, validate } = require('./validators');

const app = express();
app.use(cors());
app.use(express.json());

// Standard response envelope [cite: 55]
const formatResponse = (data = null, error = null) => ({ data, error });

// POST /patients
app.post('/patients', patientValidationRules(), validate, (req, res) => {
const patient_id = crypto.randomUUID();
  const {
    first_name, last_name, date_of_birth, sex, phone_number, email,
    address_line_1, address_line_2, city, state, zip_code,
    insurance_provider, insurance_member_id, preferred_language,
    emergency_contact_name, emergency_contact_phone
  } = req.body;

  const sql = `
    INSERT INTO patients (
      patient_id, first_name, last_name, date_of_birth, sex, phone_number, email,
      address_line_1, address_line_2, city, state, zip_code, insurance_provider,
      insurance_member_id, preferred_language, emergency_contact_name, emergency_contact_phone
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `;
  
  const params = [
    patient_id, first_name, last_name, date_of_birth, sex, phone_number, email,
    address_line_1, address_line_2, city, state, zip_code, insurance_provider,
    insurance_member_id, preferred_language || 'English', emergency_contact_name, emergency_contact_phone
  ];

  db.run(sql, params, function(err) {
    if (err) return res.status(500).json(formatResponse(null, err.message));
    
    db.get(`SELECT * FROM patients WHERE patient_id = ?`, [patient_id], (err, row) => {
      res.status(201).json(formatResponse(row, null));
    });
  });
});

// GET /patients [cite: 51]
app.get('/patients', (req, res) => {
  const { last_name, date_of_birth, phone_number } = req.query;
  let sql = `SELECT * FROM patients WHERE deleted_at IS NULL`;
  let params = [];

  if (last_name) {
    sql += ` AND last_name LIKE ?`;
    params.push(`%${last_name}%`);
  }
  if (date_of_birth) {
    sql += ` AND date_of_birth = ?`;
    params.push(date_of_birth);
  }
  if (phone_number) {
    sql += ` AND phone_number = ?`;
    params.push(phone_number);
  }

  db.all(sql, params, (err, rows) => {
    if (err) return res.status(500).json(formatResponse(null, err.message));
    res.status(200).json(formatResponse(rows, null));
  });
});

// GET /patients/:id [cite: 51]
app.get('/patients/:id', (req, res) => {
  db.get(`SELECT * FROM patients WHERE patient_id = ? AND deleted_at IS NULL`, [req.params.id], (err, row) => {
    if (err) return res.status(500).json(formatResponse(null, err.message));
    if (!row) return res.status(404).json(formatResponse(null, "Patient not found"));
    res.status(200).json(formatResponse(row, null));
  });
});

// PUT /patients/:id (Partial updates) [cite: 51]
app.put('/patients/:id', patientUpdateRules(), validate, (req, res) => {
  const updates = [];
  const params = [];
  
  for (const [key, value] of Object.entries(req.body)) {
    updates.push(`${key} = ?`);
    params.push(value);
  }
  
  if (updates.length === 0) return res.status(400).json(formatResponse(null, "No fields to update"));
  
  updates.push(`updated_at = CURRENT_TIMESTAMP`);
  params.push(req.params.id);

  const sql = `UPDATE patients SET ${updates.join(', ')} WHERE patient_id = ? AND deleted_at IS NULL`;
  
  db.run(sql, params, function(err) {
    if (err) return res.status(500).json(formatResponse(null, err.message));
    if (this.changes === 0) return res.status(404).json(formatResponse(null, "Patient not found"));
    
    db.get(`SELECT * FROM patients WHERE patient_id = ?`, [req.params.id], (err, row) => {
      res.status(200).json(formatResponse(row, null));
    });
  });
});

// DELETE /patients/:id (Soft-delete) [cite: 51]
app.delete('/patients/:id', (req, res) => {
  const sql = `UPDATE patients SET deleted_at = CURRENT_TIMESTAMP WHERE patient_id = ? AND deleted_at IS NULL`;
  
  db.run(sql, [req.params.id], function(err) {
    if (err) return res.status(500).json(formatResponse(null, err.message));
    if (this.changes === 0) return res.status(404).json(formatResponse(null, "Patient not found"));
    res.status(200).json(formatResponse({ message: "Patient soft-deleted successfully" }, null));
  });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
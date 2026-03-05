const { body, validationResult } = require('express-validator');

// Helper to check if a date string (MM/DD/YYYY) is in the future
const isFutureDate = (dateString) => {
  const [month, day, year] = dateString.split('/');
  const inputDate = new Date(`${year}-${month}-${day}`);
  const today = new Date();
  
  // Reset time portions to accurately compare just the dates
  today.setHours(0, 0, 0, 0);
  inputDate.setHours(0, 0, 0, 0);
  
  return inputDate > today;
};

// Validation rules for POST /patients (Create) [cite: 51]
const patientValidationRules = () => {
  return [
    // Required Core Identity Fields 
    body('first_name')
      .isString().trim()
      .isLength({ min: 1, max: 50 }).withMessage('Must be 1-50 characters')
      .matches(/^[a-zA-Z\-\']+$/).withMessage('Alphabetic characters, hyphens, and apostrophes only'),
    
    body('last_name')
      .isString().trim()
      .isLength({ min: 1, max: 50 }).withMessage('Must be 1-50 characters')
      .matches(/^[a-zA-Z\-\']+$/).withMessage('Alphabetic characters, hyphens, and apostrophes only'),
    
    body('date_of_birth')
      .matches(/^(0[1-9]|1[0-2])\/(0[1-9]|[12]\d|3[01])\/\d{4}$/).withMessage('Must be in MM/DD/YYYY format')
      .custom((value) => {
        if (isFutureDate(value)) {
          throw new Error('Date of birth cannot be in the future'); // [cite: 30, 39]
        }
        return true;
      }),
    
    body('sex')
      .isIn(['Male', 'Female', 'Other', 'Decline to Answer']).withMessage('Must be Male, Female, Other, or Decline to Answer'),
    
    // Contact and Location 
    body('phone_number')
      .matches(/^\d{10}$/).withMessage('Must be a valid 10-digit U.S. phone number'),
    
    body('email')
      .optional({ checkFalsy: true })
      .isEmail().withMessage('Must be a valid email format'),
    
    body('address_line_1')
      .isString().trim().notEmpty().withMessage('Street address is required'),
    
    body('address_line_2')
      .optional({ checkFalsy: true }).isString().trim(),
    
    body('city')
      .isString().trim()
      .isLength({ min: 1, max: 100 }).withMessage('Must be 1-100 characters'),
    
    body('state')
      .isString().trim()
      .matches(/^[A-Z]{2}$/).withMessage('Must be a valid 2-letter uppercase U.S. state abbreviation'),
    
    body('zip_code')
      .matches(/^\d{5}(-\d{4})?$/).withMessage('Must be a 5-digit or ZIP+4 U.S. format'),
    
    // Optional Insurance & Preferences 
    body('insurance_provider')
      .optional({ checkFalsy: true }).isString().trim(),
    
    body('insurance_member_id')
      .optional({ checkFalsy: true }).isString().trim(),
    
    body('preferred_language')
      .optional({ checkFalsy: true }).isString().trim(),
    
    body('emergency_contact_name')
      .optional({ checkFalsy: true }).isString().trim(),
    
    body('emergency_contact_phone')
      .optional({ checkFalsy: true })
      .matches(/^\d{10}$/).withMessage('Must be a valid 10-digit U.S. phone number')
  ];
};

// Validation rules for PUT /patients/:id (Partial Update) [cite: 51]
const patientUpdateRules = () => {
  // We reuse the creation rules but make every field optional for partial updates.
  return patientValidationRules().map(rule => rule.optional());
};

// Middleware to format validation errors into the required envelope [cite: 55]
const validate = (req, res, next) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    // Format error message to be readable
    const errorString = errors.array().map(err => `${err.path}: ${err.msg}`).join(' | ');
    
    return res.status(422).json({
      data: null,
      error: errorString
    });
  }
  next();
};

module.exports = {
  patientValidationRules,
  patientUpdateRules,
  validate
};
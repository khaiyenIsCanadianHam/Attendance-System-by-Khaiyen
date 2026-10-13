/**
 * Run createAttendanceRegistrationForm() in a standalone Google Apps Script project.
 * Creates a registration form and a private linked response spreadsheet.
 * Review the form's email verification/access settings before publishing it.
 */
function createAttendanceRegistrationForm() {
  // Replace these options with your school's actual sections before running.
  const sections = ['Section A', 'Section B'];
  const form = FormApp.create('Student Registration for Attendance');
  form.setDescription(
    'Register for the school attendance system. Provide your official school details. ' +
    'Your teacher will review your submission before issuing your QR code. ' +
    'Submitting this form does not record attendance.'
  );
  form.setCollectEmail(true);
  form.setLimitOneResponsePerUser(true);
  form.setAllowResponseEdits(false);
  form.setPublishingSummary(false);
  form.setShowLinkToRespondAgain(false);
  form.setConfirmationMessage(
    'Your registration was received. Your teacher will verify your details and ' +
    'provide a QR code after approval. This submission is not an attendance record.'
  );

  form.addTextItem()
    .setTitle('full_name')
    .setHelpText('Enter your full name exactly as recorded in the official school roster.')
    .setRequired(true);
  form.addListItem()
    .setTitle('section')
    .setHelpText('Select your current section.')
    .setChoiceValues(sections)
    .setRequired(true);
  form.addTextItem()
    .setTitle('lrn')
    .setHelpText('Enter your 12-digit Learner Reference Number. Preserve any leading zeros.')
    .setValidation(FormApp.createTextValidation()
      .requireTextMatchesPattern('^[0-9]{12}$')
      .setHelpText('Your LRN must contain exactly 12 digits.')
      .build())
    .setRequired(true);

  const spreadsheet = SpreadsheetApp.create('Attendance Registration Responses');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, spreadsheet.getId());

  // The approval column belongs in the private Sheet, never as a form question.
  // Add it after the first test response using the README's setup instructions.
  console.log('Form editor: ' + form.getEditUrl());
  console.log('Responder link: ' + form.getPublishedUrl());
  console.log('Private response spreadsheet: ' + spreadsheet.getUrl());
  console.log('Next: select Verified email collection, review responder access, publish, ' +
    'submit a test response, then add the teacher-only approved checkbox column.');
}

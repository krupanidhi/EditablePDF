DECLARE @CEstandardchecklist TABLE
(
    questionid      UNIQUEIDENTIFIER,
    Questiontext    VARCHAR(MAX),
    answerfieldid   UNIQUEIDENTIFIER,
    commentfielid   UNIQUEIDENTIFIER
);

INSERT INTO @CEstandardchecklist
VALUES
('36892C34-42DC-4332-B260-0001EE23F107', '1. Does the application contain all required attachments listed in the guidance?','3A83CE99-F295-4A1C-8D77-E69D70F1678D','5491FB94-F14B-4E92-8AA5-95086D481A09'),
('CCC902D6-F644-4375-A1F7-4C701032083E', '2. Is the applicant an eligible entity as mentioned in the guidance (e.g. non-profit, governmental unit)?','A126A019-D2A3-403E-BCAD-363D91DB1741','07A63DF3-37AC-49BC-94F5-35512B9FC72E');


/*=========================================================
  HRSA-24* ONLY (Program Check List):

    1) Force the "Form 1A patient projection <95% ..." question to 19
       EVEN IF an original number exists in the source (e.g., "4. ...").

       IMPORTANT: matching is done via a stable prefix using LIKE
       so it still matches whether the source has <br>, period at end,
       extra spaces, etc.

    2) If ParsedQuestionNumber IS NULL:
       - Project Narrative => 1
       - Attachment 6 => 2
       - Attachment 11 => 3
       - Completeness Checklist => 20
       - OPPD eligible => 21

    3) Else (ParsedQuestionNumber IS NOT NULL) for ALL OTHER questions:
       - add +3 to parsed number

  All other NOFOs/forms:
    - Keep parsed number when present
    - If Program Check List and NULL number: HRSA-26 / HRSA-25 mappings retained
=========================================================*/

SELECT
    q.AnnouncementNumber,
    q.ApplicationTrackingNo,
    q.QuestionNumber,
    q.QuestionText,
    q.Answer,
    q.Comments,
    q.Form
FROM
(
    SELECT
        t.AnnouncementNumber,
        t.ApplicationTrackingNo,
        t.QuestionText,
        t.Answer,
        t.Comments,
        t.Form,

        CASE
            /* =========================
               HRSA-24* PROGRAM CHECK LIST: special rules
            ==========================*/

            /* (A) Force Form 1A question to 19 ALWAYS (NULL or NOT NULL)
               Use LIKE on a stable prefix so it matches:
                 - with/without <br>
                 - with/without trailing period
                 - different spacing around <br>
            */
            WHEN t.Form = 'Program Check List'
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-24%'
                 AND t.QuestionTextKey LIKE
                     'If the Form 1A patient projection is less than 95% of the SAAT Patient Target, is the annual SAC funding request reduced accordingly in the SF-424A and Budget Narrative?%This question does not impact application completeness or eligibility%'
            THEN '19'

            /* (B) NULL number -> explicit mappings */
            WHEN t.Form = 'Program Check List'
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-24%'
                 AND t.ParsedQuestionNumber IS NULL
                 AND t.QuestionTextNormalized = 'Project Narrative'
            THEN '1'

            WHEN t.Form = 'Program Check List'
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-24%'
                 AND t.ParsedQuestionNumber IS NULL
                 AND t.QuestionTextNormalized =
                     'Attachment 6: Co-Applicant Agreement (required for new public center applicants that have a co-applicant board)'
            THEN '2'

            WHEN t.Form = 'Program Check List'
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-24%'
                 AND t.ParsedQuestionNumber IS NULL
                 AND t.QuestionTextNormalized =
                     'Attachment 11: Evidence of Nonprofit or Public Center Status (new applicants)'
            THEN '3'

            WHEN t.Form = 'Program Check List'
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-24%'
                 AND t.ParsedQuestionNumber IS NULL
                 AND t.QuestionTextNormalized =
                     'Is the application complete based on the required documents mentioned in the Completeness Checklist?'
            THEN '20'

            WHEN t.Form = 'Program Check List'
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-24%'
                 AND t.ParsedQuestionNumber IS NULL
                 AND t.QuestionTextNormalized =
                     'Based on BPHC OPPD review, is the applicant eligible?'
            THEN '21'

            /* (C) HRSA-24: all other NOT NULL numbered questions -> add +3 */
            WHEN t.Form = 'Program Check List'
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-24%'
                 AND t.ParsedQuestionNumber IS NOT NULL
            THEN CONVERT(VARCHAR(10), TRY_CONVERT(INT, t.ParsedQuestionNumber) + 3)


            /* =========================
               OTHER CASES (non HRSA-24 / non Program / etc.)
            ==========================*/

            /* Keep parsed number when present */
            WHEN t.ParsedQuestionNumber IS NOT NULL
            THEN t.ParsedQuestionNumber

            /* HRSA-26* Program Check List mappings (only when number is NULL) */
            WHEN t.Form = 'Program Check List'
                 AND t.ParsedQuestionNumber IS NULL
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-26%'
                 AND t.QuestionTextKey LIKE
                     'If the Form 1A patient projection is less than 95% of the SAAT Patient Target, is the annual SAC funding request reduced accordingly in the SF-424A and Budget Narrative?%This question does not impact application completeness or eligibility%'
            THEN '20'

            WHEN t.Form = 'Program Check List'
                 AND t.ParsedQuestionNumber IS NULL
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-26%'
                 AND t.QuestionTextNormalized =
                     'Is the application complete based on the required documents mentioned in the Completeness and Eligibility Checklist?'
            THEN '21'

            WHEN t.Form = 'Program Check List'
                 AND t.ParsedQuestionNumber IS NULL
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-26%'
                 AND t.QuestionTextNormalized =
                     'Based on BPHC OPPD review, is the applicant eligible?'
            THEN '22'

            /* HRSA-25* Program Check List mappings (only when number is NULL) */
            WHEN t.Form = 'Program Check List'
                 AND t.ParsedQuestionNumber IS NULL
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-25%'
                 AND t.QuestionTextKey LIKE
                     'If the Form 1A patient projection is less than 95% of the SAAT Patient Target, is the annual SAC funding request reduced accordingly in the SF-424A and Budget Narrative?%This question does not impact application completeness or eligibility%'
            THEN '18'

            WHEN t.Form = 'Program Check List'
                 AND t.ParsedQuestionNumber IS NULL
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-25%'
                 AND t.QuestionTextNormalized =
                     'Is the application complete based on the required documents mentioned in the Completeness and Eligibility Checklist?'
            THEN '19'

            WHEN t.Form = 'Program Check List'
                 AND t.ParsedQuestionNumber IS NULL
                 AND UPPER(t.AnnouncementNumber) LIKE 'HRSA-25%'
                 AND t.QuestionTextNormalized =
                     'Based on BPHC OPPD review, is the applicant eligible?'
            THEN '20'

            ELSE NULL
        END AS QuestionNumber

    FROM
    (
        /* =========================
           UNION SOURCE + PARSING + NORMALIZATION
        ==========================*/
        SELECT
            src.AnnouncementNumber,
            src.ApplicationTrackingNo,
            src.Form,
            src.Answer,
            src.Comments,

            /* Parsed number only when string STARTS with digits immediately followed by '.' */
            CASE
                WHEN src.dot_pos > 1
                     AND SUBSTRING(src.q_trim, 1, 1) LIKE '[0-9]'
                     AND PATINDEX('%[^0-9]%', LEFT(src.q_trim, src.dot_pos - 1)) = 0
                THEN LEFT(src.q_trim, src.dot_pos - 1)
                ELSE NULL
            END AS ParsedQuestionNumber,

            /* Parsed/split question text */
            CASE
                WHEN src.dot_pos > 1
                     AND SUBSTRING(src.q_trim, 1, 1) LIKE '[0-9]'
                     AND PATINDEX('%[^0-9]%', LEFT(src.q_trim, src.dot_pos - 1)) = 0
                THEN LTRIM(SUBSTRING(src.q_trim, src.dot_pos + 1, LEN(src.q_trim)))
                ELSE src.q_trim
            END AS QuestionText,

            /* Normalize for exact equals matching (strip <br> variants) */
            LTRIM(RTRIM(
                REPLACE(REPLACE(REPLACE(
                    CASE
                        WHEN src.dot_pos > 1
                             AND SUBSTRING(src.q_trim, 1, 1) LIKE '[0-9]'
                             AND PATINDEX('%[^0-9]%', LEFT(src.q_trim, src.dot_pos - 1)) = 0
                        THEN LTRIM(SUBSTRING(src.q_trim, src.dot_pos + 1, LEN(src.q_trim)))
                        ELSE src.q_trim
                    END
                , '<br />', ''), '<br/>', ''), '<br>', '')
            )) AS QuestionTextNormalized,

            /* Key used for LIKE matching (convert <br> to space, collapse double spaces a few times) */
            LTRIM(RTRIM(
                REPLACE(REPLACE(REPLACE(
                    REPLACE(REPLACE(REPLACE(
                        REPLACE(REPLACE(REPLACE(
                            CASE
                                WHEN src.dot_pos > 1
                                     AND SUBSTRING(src.q_trim, 1, 1) LIKE '[0-9]'
                                     AND PATINDEX('%[^0-9]%', LEFT(src.q_trim, src.dot_pos - 1)) = 0
                                THEN LTRIM(SUBSTRING(src.q_trim, src.dot_pos + 1, LEN(src.q_trim)))
                                ELSE src.q_trim
                            END
                        , '<br />', ' '), '<br/>', ' '), '<br>', ' ')
                    , '  ', ' '), '  ', ' '), '  ', ' ')
                , '  ', ' '), '  ', ' '), '  ', ' ')
            )) AS QuestionTextKey

        FROM
        (
            /* =========================
               STANDARD CHECK LIST (GEMS)
            ==========================*/
            SELECT
                fc.AnnouncementNumber,
                ap.ApplicationTrackingNo,
                'Standard Check list' AS Form,
                LTRIM(CEQ.Questiontext) AS q_trim,
                CHARINDEX('.', LTRIM(CEQ.Questiontext)) AS dot_pos,
                ans.Answer AS Answer,
                '' AS Comments
            FROM gems..fundingcycles fc
            JOIN gems..applications ap
                ON ap.FundingCycleId = fc.fundingcycleid
            JOIN gems..ApplicationCompletenessEligibilityReview ce
                ON ce.applicationid = ap.applicationid
            JOIN gems..ChecklistInstance CI
                ON CI.ResourceValue = ce.applicationcompletenessEligibilityReviewid
            JOIN gems..ChecklistDefinition CD
                ON CD.ChecklistDefinitionId = CI.ChecklistDefinitionId
               AND CD.version = CI.ChecklistDefinitionVersion
            JOIN gems..ChecklistInstanceAnswer ans
                ON ans.ChecklistInstanceId = CI.ChecklistInstanceId
            JOIN @CEstandardchecklist CEQ
                ON CEQ.questionid = ans.QuestionId
               AND ans.FieldId = CEQ.answerfieldid
            WHERE fc.AnnouncementNumber IN
            (
                'HRSA-26-002','HRSA-26-007','HRSA-26-006',
                'HRSA-25-087','HRSA-25-013','HRSA-25-015',
                'HRSA-25-012','HRSA-25-014','HRSA-25-016',
                'HRSA-25-017','HRSA-24-066','HRSA-24-068',
                'HRSA-24-069','HRSA-24-067','HRSA-24-071',
                'HRSA-24-104','HRSA-24-070','HRSA-24-087'
            )

            UNION ALL

            /* =========================
               PROGRAM CHECK LIST (BHCMIS)
            ==========================*/
            SELECT DISTINCT
                fc.AnnouncementNumber,
                ar.EHBTrackingNo AS ApplicationTrackingNo,
                'Program Check List' AS Form,
                LTRIM(luQuestion.DisplayValue) AS q_trim,
                CHARINDEX('.', LTRIM(luQuestion.DisplayValue)) AS dot_pos,
                luAnswer.DisplayValue AS Answer,
                checklist.Comments AS Comments
            FROM [BHCMIS]..GAM_ApplicationCompletenessEligibilityReview_P ar
            JOIN [BHCMIS]..GAM_Application_P a
                ON a.EHBApplicationId = ar.EHBApplicationId
            JOIN [BHCMIS]..CMN_FundingCycle_P fc
                ON fc.PackageId = a.PackageId
               AND fc.CompletenessEligibilityPackageId = ar.PackageId
               AND a.FundingCycleId = fc.FundingCycleId
            JOIN [BHCMIS]..GAM_Checklist_P checklist
                ON checklist.ResourceValue = ar.ApplicationReviewId
               AND checklist.PackageId = ar.PackageId
            JOIN [BHCMIS]..GAM_ChecklistSection_P section
                ON section.ChecklistId = checklist.ChecklistId
            JOIN [BHCMIS]..GAM_ChecklistQuestion_P question
                ON question.ChecklistSectionId = section.ChecklistSectionId
            JOIN [BHCMIS]..LU_GAM_ChecklistQuestion luQuestion
                ON luQuestion.LookupCode = question.ChecklistQuestionCode
            JOIN [BHCMIS]..LU_GAM_ChecklistQuestion_LU_GAM_ChecklistAnswer_R relAnswer
                ON relAnswer.ChecklistQuestionCode = question.ChecklistQuestionCode
            LEFT JOIN [BHCMIS]..GAM_ChecklistAnswer_P answer
                ON answer.ChecklistQuestionId = question.ChecklistQuestionId
            LEFT JOIN [BHCMIS]..LU_GAM_ChecklistAnswer luAnswer
                ON luAnswer.LookupCode = answer.ChecklistAnswerCode
            WHERE fc.AnnouncementNumber IN
            (
                'HRSA-26-002','HRSA-26-007','HRSA-26-006',
                'HRSA-25-087','HRSA-25-013','HRSA-25-015',
                'HRSA-25-012','HRSA-25-014','HRSA-25-016',
                'HRSA-25-017','HRSA-24-066','HRSA-24-068',
                'HRSA-24-069','HRSA-24-067','HRSA-24-071',
                'HRSA-24-104','HRSA-24-070','HRSA-24-087'
            )
        ) src
    ) t
) q
ORDER BY
    q.ApplicationTrackingNo ASC,
    TRY_CONVERT(INT, q.QuestionNumber) ASC,
    q.Form DESC;